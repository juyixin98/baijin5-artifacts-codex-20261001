"""Streaming ingest sessions.

A stream session accumulates excitation/response blocks before a
one-shot estimation. The state machine is explicit:

    OPEN --seal()--> SEALED --estimate()--> ESTIMATED
      |                 |
      +--abort()--> ABORTED (terminal from any non-terminal state)

Illegal transitions raise StateConflictError. Appending beyond the
configured sample budget raises ResourceExhaustedError. This module owns
stream state only; it delegates validation and estimation to contracts
and estimator, so the data/error contract is identical to the one-shot
path.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field

import numpy as np

from .contracts import DEFAULT_MAX_SAMPLES, EstimateParams, validate_sample_block
from .errors import ResourceExhaustedError, StateConflictError
from .estimator import EstimateResult, estimate_fir
from .runlog import RunLogger, new_run_id


class StreamState(str, enum.Enum):
    OPEN = "open"
    SEALED = "sealed"
    ESTIMATED = "estimated"
    ABORTED = "aborted"


_TERMINAL = (StreamState.ESTIMATED, StreamState.ABORTED)

_session_counter = itertools.count(1)


@dataclass
class StreamSession:
    stream_id: str
    max_samples: int = DEFAULT_MAX_SAMPLES
    state: StreamState = StreamState.OPEN
    _excitation_parts: list[np.ndarray] = field(default_factory=list)
    _response_parts: list[np.ndarray] = field(default_factory=list)
    n_samples: int = 0

    def append(self, excitation: object, response: object) -> int:
        """Append one block. Returns the cumulative sample count."""
        if self.state is not StreamState.OPEN:
            raise StateConflictError(
                f"cannot append to stream in state {self.state.value}",
                detail={"stream_id": self.stream_id, "state": self.state.value},
            )
        block = validate_sample_block(excitation, response)
        if self.n_samples + block.n_samples > self.max_samples:
            raise ResourceExhaustedError(
                "stream sample budget exceeded",
                detail={
                    "stream_id": self.stream_id,
                    "n_samples": self.n_samples + block.n_samples,
                    "max_samples": self.max_samples,
                },
            )
        self._excitation_parts.append(block.excitation)
        self._response_parts.append(block.response)
        self.n_samples += block.n_samples
        return self.n_samples

    def seal(self) -> StreamState:
        if self.state is not StreamState.OPEN:
            raise StateConflictError(
                f"cannot seal stream in state {self.state.value}",
                detail={"stream_id": self.stream_id, "state": self.state.value},
            )
        if self.n_samples == 0:
            raise StateConflictError(
                "cannot seal an empty stream",
                detail={"stream_id": self.stream_id},
            )
        self.state = StreamState.SEALED
        return self.state

    def abort(self) -> StreamState:
        if self.state in _TERMINAL:
            raise StateConflictError(
                f"cannot abort stream in terminal state {self.state.value}",
                detail={"stream_id": self.stream_id, "state": self.state.value},
            )
        self.state = StreamState.ABORTED
        return self.state

    def estimate(
        self,
        params: EstimateParams,
        *,
        logger: RunLogger | None = None,
        run_id: str | None = None,
    ) -> EstimateResult:
        if self.state is not StreamState.SEALED:
            raise StateConflictError(
                f"cannot estimate from stream in state {self.state.value}; seal it first",
                detail={"stream_id": self.stream_id, "state": self.state.value},
            )
        excitation = np.concatenate(self._excitation_parts)
        response = np.concatenate(self._response_parts)
        result = estimate_fir(
            excitation,
            response,
            params,
            max_samples=self.max_samples,
            logger=logger,
            run_id=run_id,
        )
        self.state = StreamState.ESTIMATED
        return result


class StreamRegistry:
    """In-memory registry of stream sessions keyed by stream id."""

    def __init__(self, *, max_samples: int = DEFAULT_MAX_SAMPLES, max_sessions: int = 1024) -> None:
        self.max_samples = max_samples
        self.max_sessions = max_sessions
        self._sessions: dict[str, StreamSession] = {}

    def create(self) -> StreamSession:
        live = [s for s in self._sessions.values() if s.state not in _TERMINAL]
        if len(live) >= self.max_sessions:
            raise ResourceExhaustedError(
                "too many live stream sessions",
                detail={"max_sessions": self.max_sessions},
            )
        stream_id = f"stream-{next(_session_counter):06d}-{new_run_id()[:8]}"
        session = StreamSession(stream_id=stream_id, max_samples=self.max_samples)
        self._sessions[stream_id] = session
        return session

    def get(self, stream_id: str) -> StreamSession | None:
        return self._sessions.get(stream_id)
