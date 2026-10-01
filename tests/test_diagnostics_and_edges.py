"""Diagnostics helpers, independent schedule, and defensive error paths."""

from __future__ import annotations

import json

import pytest

from app.diagnostics import dump_event_trail, explain_decision
from app.errors import (
    ComputationError,
    InputValidationError,
    IntegrityError,
)
from app.simulation import fixed_signal_stream, independent_gamma, run_experiment
from app.statistics import normalized_schedule


def test_explain_decision_states_causal_ordering():
    pending = {"idx": 1, "status": "pending"}
    assert "p-value not yet observed" in explain_decision(pending)
    decided = {
        "idx": 2,
        "status": "decided",
        "hypothesis_id": "H2",
        "threshold": 0.0007,
        "p_value": 0.5,
        "rejected": False,
    }
    text = explain_decision(decided)
    assert "frozen BEFORE observing p_t" in text
    assert "do not reject" in text


def test_dump_event_trail_roundtrip(tmp_path):
    path = tmp_path / "trail.json"
    events = [{"seq": 1, "kind": "decide", "payload": {"p": 0.5}}]
    dump_event_trail(events, str(path))
    assert json.loads(path.read_text()) == events


def test_independent_gamma_matches_kernel_schedule():
    import math

    sched = normalized_schedule(1000)
    for j in (1, 2, 3, 50, 500, 1000):
        assert math.isclose(independent_gamma(j, 1000), float(sched[j - 1]), rel_tol=1e-12)
    assert independent_gamma(1001, 1000) == 0.0
    assert independent_gamma(0, 1000) == 0.0


def test_fixed_signal_stream_validates_positions():
    with pytest.raises(ValueError):
        fixed_signal_stream(3, [5])
    with pytest.raises(ValueError):
        fixed_signal_stream(3, [-1])


def test_run_experiment_rejects_unknown_stream_kind():
    with pytest.raises(ValueError):
        run_experiment("bad", 5, 2, pi1=0.5, stream_kind="nonsense")


def test_schedule_rejects_bad_horizon():
    with pytest.raises(InputValidationError):
        normalized_schedule(0)
    with pytest.raises(InputValidationError):
        normalized_schedule(10**9)


# ----------------------------------------------------------- store integrity --


def test_flip_of_rejected_flag_is_detected(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    with store._conn:  # noqa: SLF001 - deliberate tamper
        store._conn.execute(
            "UPDATE steps SET rejected=0 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(IntegrityError):
        store.replay("r")


def test_row_hash_tampering_is_detected(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    with store._conn:  # noqa: SLF001 - deliberate tamper
        store._conn.execute(
            "UPDATE steps SET row_hash=? WHERE run_id='r' AND idx=1",
            ("f" * 64,),
        )
    with pytest.raises(IntegrityError):
        store.replay("r")


def test_run_head_hash_tampering_is_detected(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    with store._conn:  # noqa: SLF001 - deliberate tamper
        store._conn.execute(
            "UPDATE runs SET head_hash=? WHERE run_id='r'", ("a" * 64,)
        )
    with pytest.raises(IntegrityError):
        store.replay("r")


def test_wealth_before_tampering_is_detected(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    with store._conn:  # noqa: SLF001 - deliberate tamper of diagnostic column
        store._conn.execute(
            "UPDATE steps SET wealth_before=wealth_before+1 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(IntegrityError):
        store.replay("r")


def test_gamma_tampering_is_detected(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    with store._conn:  # noqa: SLF001 - frozen schedule constant cannot be altered
        store._conn.execute(
            "UPDATE steps SET gamma_t=gamma_t*2 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(IntegrityError):
        store.replay("r")


def test_replay_with_pending_slot_skips_it(store):
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    store.decide("r", "h1", 0.0005)
    store.reserve("r", "h2")  # left pending
    report = store.replay("r")
    assert report["decisions_checked"] == 1
    assert report["rejections"] == 1


def test_get_unknown_step_is_not_found(store):
    from app.errors import NotFoundError
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(), run_id="r")
    with pytest.raises(NotFoundError):
        store.get_step("r", 99)


def test_pending_threshold_tamper_is_computation_failure(store):
    """If a pre-committed threshold no longer matches recomputation, deciding
    fails as COMPUTATION_FAILED rather than silently using either value."""
    from app.errors import ComputationError
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    store.reserve("r", "h1")
    with store._conn:  # noqa: SLF001 - deliberate corruption of pending row
        store._conn.execute(
            "UPDATE steps SET threshold=threshold*100 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(ComputationError) as ei:
        store.decide("r", "h1", 0.0005)
    assert ei.value.code == "COMPUTATION_FAILED"
    # The corrupted slot is not marked decided: state remains safely pending.
    row = store.get_step("r", 1)
    assert row["status"] == "pending"
    assert row["p_value"] is None


def test_duplicate_run_id_is_state_conflict(store):
    from app.errors import StateConflictError
    from app.statistics import LordConfig

    store.create_run(LordConfig.create(), run_id="r")
    with pytest.raises(StateConflictError) as ei:
        store.create_run(LordConfig.create(), run_id="r")
    assert ei.value.code == "STATE_CONFLICT"
