"""Stream state: chunked signal upload sessions with an explicit state machine.

A session lets a caller upload excitation/response in chunks and then run
one estimation over the concatenated stream. The lifecycle is::

    OPEN --(finalize)--> FINALIZED

Illegal transitions raise :class:`StateConflictError` so that state
conflicts are distinguishable from input and resource errors:

- appending to a finalized session
- finalizing twice
- finalizing a session with no data
- operating on an unknown session id

Accumulated samples are bounded by ``config.max_samples``; exceeding the
bound raises :class:`ResourceExhaustedError`.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

from ..config import AppConfig
from ..errors import InputError, ResourceExhaustedError, StateConflictError


class SessionState(str, Enum):
    OPEN = "OPEN"
    FINALIZED = "FINALIZED"


@dataclass
class StreamSession:
    session_id: str
    state: SessionState = SessionState.OPEN
    created_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    excitation: list[float] = field(default_factory=list)
    response: list[float] = field(default_factory=list)
    n_chunks: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "created_utc": self.created_utc,
            "n_samples": len(self.excitation),
            "n_chunks": self.n_chunks,
        }


class SessionStore:
    """Thread-safe in-memory store of stream sessions."""

    def __init__(self, config: AppConfig | None = None):
        self._config = config or AppConfig()
        self._sessions: dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def create(self) -> StreamSession:
        session = StreamSession(session_id=uuid.uuid4().hex[:16])
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def _get_locked(self, session_id: str) -> StreamSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise StateConflictError(
                "no such stream session",
                reason="session_not_found",
                detail={"session_id": session_id},
            )
        return session

    def get(self, session_id: str) -> StreamSession:
        with self._lock:
            return self._get_locked(session_id)

    def append_chunk(
        self, session_id: str, excitation: Any, response: Any
    ) -> StreamSession:
        x = np.asarray(excitation, dtype=float)
        y = np.asarray(response, dtype=float)
        if x.ndim != 1 or y.ndim != 1 or x.size == 0:
            raise InputError(
                "chunk excitation/response must be non-empty 1-D sequences",
                reason="chunk_invalid",
            )
        if x.size != y.size:
            raise InputError(
                "chunk excitation and response must have equal length",
                reason="length_mismatch",
                detail={"n_excitation": int(x.size), "n_response": int(y.size)},
            )
        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
            raise InputError(
                "chunk samples must be finite", reason="chunk_non_finite"
            )
        with self._lock:
            session = self._get_locked(session_id)
            if session.state is not SessionState.OPEN:
                raise StateConflictError(
                    "cannot append to a finalized session",
                    reason="session_finalized",
                    detail=session.snapshot(),
                )
            new_total = len(session.excitation) + int(x.size)
            if new_total > self._config.max_samples:
                raise ResourceExhaustedError(
                    "accumulated stream exceeds the configured maximum samples",
                    reason="too_many_samples",
                    detail={
                        "accumulated": new_total,
                        "max_samples": self._config.max_samples,
                    },
                )
            session.excitation.extend(float(v) for v in x)
            session.response.extend(float(v) for v in y)
            session.n_chunks += 1
            return session

    def finalize(self, session_id: str) -> tuple[np.ndarray, np.ndarray]:
        """Close the session and return the concatenated (excitation, response)."""
        with self._lock:
            session = self._get_locked(session_id)
            if session.state is SessionState.FINALIZED:
                raise StateConflictError(
                    "session was already finalized",
                    reason="already_finalized",
                    detail=session.snapshot(),
                )
            if session.n_chunks == 0:
                raise StateConflictError(
                    "cannot finalize a session with no uploaded chunks",
                    reason="no_data",
                    detail=session.snapshot(),
                )
            session.state = SessionState.FINALIZED
            return (
                np.asarray(session.excitation, dtype=float),
                np.asarray(session.response, dtype=float),
            )
