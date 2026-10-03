"""Streaming state tests: undetermined -> ok -> unreachable transitions and
bounded snapshot history."""

import numpy as np

from dtw_service.constraints import PathConstraints
from dtw_service.diagnostics import STATUS_OK, STATUS_UNDETERMINED, STATUS_UNREACHABLE
from dtw_service.stream import StreamingDtwState


def _state(**kwargs):
    kwargs.setdefault("constraints", PathConstraints(window=10, max_run=2))
    kwargs.setdefault("buffer_size", 16)
    kwargs.setdefault("snapshot_history", 4)
    return StreamingDtwState(**kwargs)


def test_empty_buffers_are_undetermined_not_failed():
    state = _state()
    snap = state.snapshot()
    assert snap.status == STATUS_UNDETERMINED
    assert "insufficient data" in snap.reason


def test_snapshot_after_pushes_aligns_buffers():
    state = _state()
    for t in range(8):
        frame = [float(np.sin(0.3 * t))]
        state.push_query(frame)
        state.push_reference(frame)
    snap = state.snapshot()
    assert snap.status == STATUS_OK
    assert snap.normalized_cost is not None and snap.normalized_cost < 1e-10
    assert snap.path_length is not None and snap.path_length >= 8


def test_unreachable_buffers_reported_explicitly():
    state = _state(constraints=PathConstraints(window=1, max_run=2))
    for t in range(6):
        state.push_query([0.0])
    for t in range(6):
        state.push_reference([1.0])
    # 6 vs 6 fits the band; drop to 6 vs 12 by pushing more reference frames.
    for t in range(6):
        state.push_reference([1.0])
    snap = state.snapshot()
    assert snap.status == STATUS_UNREACHABLE
    assert snap.normalized_cost is None


def test_snapshot_history_is_bounded_and_sequenced():
    state = _state(snapshot_history=3)
    for t in range(4):
        state.push_query([float(t)])
        state.push_reference([float(t)])
    for _ in range(5):
        state.snapshot()
    history = state.snapshots
    assert len(history) == 3
    sequences = [s.sequence for s in history]
    assert sequences == sorted(sequences)
    assert all(s.request_id.startswith(state.stream_id) for s in history)
