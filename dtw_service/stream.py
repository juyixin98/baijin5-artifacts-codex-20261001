"""Streaming session state for chunked alignment.

A ``StreamSession`` accumulates feature chunks for two streams and aligns
the buffered tails on demand. The session owns only *state* (buffers,
counters, last endpoint); the numerical work is delegated to
``dtw_service.service.align`` so streaming and one-shot requests share the
exact same contract, validation, and diagnostics.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

import numpy as np

from dtw_service.contracts import AlignmentRequest, AlignmentResponse
from dtw_service.service import align
from dtw_service.settings import Settings, load_settings


@dataclass(frozen=True)
class StreamState:
    """Snapshot of one session's progress."""

    session_id: str
    buffered_a: int = 0
    buffered_b: int = 0
    n_alignments: int = 0
    last_endpoint: tuple[int, int] | None = None
    last_request_id: str | None = None


@dataclass
class StreamSession:
    """Incremental alignment session over two chunked streams."""

    window_radius: int
    settings: Settings = field(default_factory=load_settings)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        self._a: list[float] = []
        self._b: list[float] = []
        self._n_alignments = 0
        self._last_endpoint: tuple[int, int] | None = None
        self._last_request_id: str | None = None

    def push_a(self, chunk: list[float]) -> None:
        self._push(self._a, chunk, "a")

    def push_b(self, chunk: list[float]) -> None:
        self._push(self._b, chunk, "b")

    @staticmethod
    def _push(buffer: list[float], chunk: list[float], name: str) -> None:
        if not chunk:
            raise ValueError(f"empty chunk pushed to stream {name}")
        values = np.asarray(chunk, dtype=float)
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite values in chunk pushed to stream {name}")
        buffer.extend(float(x) for x in values)

    def align_tail(self, tail: int) -> AlignmentResponse:
        """Align the last ``tail`` buffered samples of each stream."""
        if tail <= 0:
            raise ValueError(f"tail must be positive, got {tail}")
        if len(self._a) < tail or len(self._b) < tail:
            raise ValueError(
                f"tail {tail} exceeds buffered lengths "
                f"({len(self._a)}, {len(self._b)})"
            )
        response = align(
            AlignmentRequest(
                sequence_a=self._a[-tail:],
                sequence_b=self._b[-tail:],
                window_radius=self.window_radius,
            ),
            settings=self.settings,
        )
        self._n_alignments += 1
        self._last_request_id = response.request_id
        if response.path:
            i, j = response.path[-1]
            self._last_endpoint = (i, j)
        return response

    def state(self) -> StreamState:
        return StreamState(
            session_id=self.session_id,
            buffered_a=len(self._a),
            buffered_b=len(self._b),
            n_alignments=self._n_alignments,
            last_endpoint=self._last_endpoint,
            last_request_id=self._last_request_id,
        )

    def state_dict(self) -> dict:
        return asdict(self.state())
