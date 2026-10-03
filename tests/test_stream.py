"""Streaming session state: chunked pushes, tail alignment, state tracking."""

import numpy as np
import pytest

from dtw_service.contracts import DecisionStatus
from dtw_service.settings import Settings
from dtw_service.stream import StreamSession

SETTINGS = Settings(sakoe_chiba_radius=10, smoothing_window=3)


def _session():
    return StreamSession(window_radius=10, settings=SETTINGS)


def test_push_and_align_tail_updates_state():
    session = _session()
    a = np.sin(np.linspace(0, 2 * np.pi, 30)).tolist()
    session.push_a(a[:15])
    session.push_a(a[15:])
    session.push_b(a)  # identical stream

    state = session.state()
    assert state.buffered_a == 30 and state.buffered_b == 30
    assert state.n_alignments == 0

    res = session.align_tail(tail=30)
    # Identical non-constant streams: zero cost, and the alignment is
    # identifiable (band distances are not all zero), so ACCEPTED.
    assert res.status is DecisionStatus.ACCEPTED
    assert res.cost == pytest.approx(0.0)

    state = session.state()
    assert state.n_alignments == 1
    assert state.last_endpoint == (29, 29)
    assert state.last_request_id == res.request_id
    assert state.session_id == session.session_id


def test_align_tail_uses_only_the_tail():
    session = _session()
    session.push_a([100.0] * 5 + [0.0, 1.0])
    session.push_b([100.0] * 5 + [0.0, 2.0])
    res = session.align_tail(tail=2)
    # Tail is the hand-computed case: cost 1.0, normalized 1/4.
    assert res.cost == pytest.approx(1.0)
    assert res.normalized_cost == pytest.approx(0.25)


def test_empty_chunk_rejected():
    session = _session()
    with pytest.raises(ValueError, match="empty chunk"):
        session.push_a([])


def test_non_finite_chunk_rejected():
    session = _session()
    with pytest.raises(ValueError, match="non-finite"):
        session.push_b([1.0, float("inf")])


def test_tail_exceeding_buffer_rejected():
    session = _session()
    session.push_a([1.0, 2.0])
    session.push_b([1.0, 2.0])
    with pytest.raises(ValueError, match="exceeds buffered"):
        session.align_tail(tail=5)


def test_failed_alignment_still_counts_attempt():
    session = StreamSession(window_radius=0, settings=SETTINGS)
    session.push_a([0.0, 0.0, 0.0])
    session.push_b([0.0, 0.0])
    res = session.align_tail(tail=2)  # radius 0, tail square: fine
    assert res.status in (DecisionStatus.ACCEPTED, DecisionStatus.INDETERMINATE)
    assert session.state().n_alignments == 1
