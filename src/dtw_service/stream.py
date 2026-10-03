"""Streaming alignment state over sliding frame buffers.

Frames arrive one at a time per side; the state keeps bounded deques and, on
demand, aligns the current buffer contents with the banded core. Snapshots are
decision points: each is recorded with a sequence number, the buffer state and
the outcome (ok / unreachable / undetermined), and a bounded history is kept
for inspection. "Undetermined" means there is not enough data to judge yet --
it is reported explicitly rather than silently coerced to a failure.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from dtw_service.constraints import PathConstraints
from dtw_service.core import UnreachablePathError, dtw_align
from dtw_service.diagnostics import (
    STATUS_OK,
    STATUS_UNDETERMINED,
    STATUS_UNREACHABLE,
    DecisionRecord,
    log_decision,
    new_request_id,
)


@dataclass(frozen=True)
class StreamSnapshot:
    sequence: int
    request_id: str
    status: str
    reason: str
    buffered_query: int
    buffered_reference: int
    normalized_cost: float | None
    path_length: int | None


@dataclass
class StreamingDtwState:
    constraints: PathConstraints
    buffer_size: int = 256
    snapshot_history: int = 32
    stream_id: str = field(default_factory=new_request_id)

    def __post_init__(self) -> None:
        self._query: deque[list[float]] = deque(maxlen=self.buffer_size)
        self._reference: deque[list[float]] = deque(maxlen=self.buffer_size)
        self._snapshots: deque[StreamSnapshot] = deque(maxlen=self.snapshot_history)
        self._frames_seen = 0

    def push_query(self, frame: list[float]) -> None:
        self._query.append([float(v) for v in frame])
        self._frames_seen += 1

    def push_reference(self, frame: list[float]) -> None:
        self._reference.append([float(v) for v in frame])
        self._frames_seen += 1

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    @property
    def snapshots(self) -> list[StreamSnapshot]:
        return list(self._snapshots)

    def snapshot(self) -> StreamSnapshot:
        """Align the current buffers and record the decision."""
        n_q, n_r = len(self._query), len(self._reference)
        sequence = len(self._snapshots) + 1
        base: dict[str, Any] = {
            "sequence": sequence,
            "request_id": f"{self.stream_id}-{sequence}",
            "buffered_query": n_q,
            "buffered_reference": n_r,
            "normalized_cost": None,
            "path_length": None,
        }

        if n_q == 0 or n_r == 0:
            snap = StreamSnapshot(
                status=STATUS_UNDETERMINED,
                reason="insufficient data: one buffer is still empty",
                **base,
            )
        else:
            try:
                result = dtw_align(
                    np.array(self._query), np.array(self._reference), self.constraints
                )
            except UnreachablePathError as exc:
                snap = StreamSnapshot(
                    status=STATUS_UNREACHABLE, reason=exc.reason, **base
                )
            else:
                snap = StreamSnapshot(
                    status=STATUS_OK,
                    reason="aligned current buffers",
                    normalized_cost=result.normalized_cost,
                    path_length=result.path_length,
                    **{k: v for k, v in base.items() if k not in {"normalized_cost", "path_length"}},
                )

        self._snapshots.append(snap)
        log_decision(
            DecisionRecord(
                request_id=snap.request_id,
                status=snap.status,
                reason=snap.reason,
                state={
                    "stream_id": self.stream_id,
                    "buffered_query": n_q,
                    "buffered_reference": n_r,
                },
            )
        )
        return snap
