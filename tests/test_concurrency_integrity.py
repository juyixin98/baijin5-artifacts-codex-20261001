"""Cross-connection concurrency and whole-record integrity tests.

The RLock only serializes one RunStore; these tests open TWO stores on the same
file (the multi-process/restart situation) and race them. Correct behavior:

* exactly one writer wins; the loser gets a classified STATE_CONFLICT,
* no committed decision is silently overwritten,
* every persisted column (incl. wealth_after and timestamps) and the run
  completion status are tamper-evident.
"""

from __future__ import annotations

import threading

import pytest

from app.errors import IntegrityError, StateConflictError
from app.statistics import LordConfig
from app.storage import RunStore


def _drive(store, run_id, ps):
    for i, p in enumerate(ps, 1):
        store.reserve(run_id, f"h{i}")
        store.decide(run_id, f"h{i}", p)


def test_double_decide_same_pending_slot_across_connections(tmp_path):
    db = tmp_path / "race.db"
    s1 = RunStore(str(db))
    s2 = RunStore(str(db))
    s1.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    s1.reserve("r", "x")  # one pending slot visible to both connections

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def decide(store, p_value, tag):
        barrier.wait()
        try:
            row = store.decide("r", "x", p_value)
            outcomes.append(f"{tag}:won:{int(row['rejected'])}")
        except StateConflictError:
            outcomes.append(f"{tag}:conflict")

    t1 = threading.Thread(target=decide, args=(s1, 0.0005, "A"))
    t2 = threading.Thread(target=decide, args=(s2, 0.9, "B"))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(outcomes) == 2
    winners = [o for o in outcomes if o.endswith(("won:0", "won:1"))]
    conflicts = [o for o in outcomes if o.endswith("conflict")]
    assert len(winners) == 1, outcomes
    assert len(conflicts) == 1, outcomes

    # The single committed row is internally consistent and replays cleanly.
    decided, pending = s1._counts("r")
    assert (decided, pending) == (1, 0)
    report = s1.replay("r")
    assert report["decisions_checked"] == 1
    # The surviving decision must be one of the two honestly submitted values.
    row = s1.get_step("r", 1)
    assert row["p_value"] in (0.0005, 0.9)
    assert bool(row["rejected"]) == (row["p_value"] <= row["threshold"])
    s1.close(); s2.close()


def test_concurrent_reserve_across_connections_single_winner(tmp_path):
    from app.storage import RunStore

    db = tmp_path / "r2.db"
    s1 = RunStore(str(db)); s2 = RunStore(str(db))
    s1.create_run(LordConfig.create(), run_id="r")
    barrier = threading.Barrier(2)
    results: list[str] = []

    def reserve(store, hid):
        barrier.wait()
        try:
            store.reserve("r", hid)
            results.append("win")
        except StateConflictError:
            results.append("conflict")

    t1 = threading.Thread(target=reserve, args=(s1, "a"))
    t2 = threading.Thread(target=reserve, args=(s2, "b"))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(results) == ["conflict", "win"]
    decided, pending = s1._counts("r")
    assert (decided, pending) == (0, 1)
    s1.close(); s2.close()


def test_wealth_after_tampering_is_detected(tmp_path):
    from app.storage import RunStore

    db = tmp_path / "w.db"
    s = RunStore(str(db))
    s.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    _drive(s, "r", [0.0005, 0.5, 0.0005])
    with s._conn:  # noqa: SLF001 - deliberate whole-record tamper
        s._conn.execute(
            "UPDATE steps SET wealth_after=999.0 WHERE run_id='r' AND idx=2"
        )
    with pytest.raises(IntegrityError):
        s.replay("r")
    s.close()


def test_timestamp_tampering_is_detected(tmp_path):
    from app.storage import RunStore

    db = tmp_path / "ts.db"
    s = RunStore(str(db))
    s.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    _drive(s, "r", [0.0005, 0.5])
    with s._conn:  # noqa: SLF001 - cosmetic timestamp is still hash-bound
        s._conn.execute(
            "UPDATE steps SET decided_at=0.0 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(IntegrityError):
        s.replay("r")
    s.close()


def test_completion_status_tampering_is_detected(tmp_path):
    from app.storage import RunStore

    db = tmp_path / "st.db"
    s = RunStore(str(db))
    # Horizon 2: two decisions legitimately complete the run.
    s.create_run(LordConfig.create(horizon=2), run_id="r")
    s.reserve("r", "a"); s.decide("r", "a", 0.5)
    s.reserve("r", "b"); s.decide("r", "b", 0.5)
    assert s.get_run("r")["status"] == "completed"
    with s._conn:  # noqa: SLF001 - flip completed back to open
        s._conn.execute("UPDATE runs SET status='open' WHERE run_id='r'")
    with pytest.raises(IntegrityError):
        s.replay("r")
    s.close()


def test_tampered_pvalue_classified_as_integrity_not_input_error(tmp_path):
    """During an AUDIT a corrupted (invalid) stored p-value is INTEGRITY_ERROR,
    not the INPUT_ERROR a fresh bad submission would produce."""
    from app.storage import RunStore

    db = tmp_path / "p.db"
    s = RunStore(str(db))
    s.create_run(LordConfig.create(alpha=0.05, w0=0.045), run_id="r")
    _drive(s, "r", [0.0005, 0.5])
    with s._conn:  # noqa: SLF001 - corrupt stored p-value to invalid
        s._conn.execute(
            "UPDATE steps SET p_value=0.0 WHERE run_id='r' AND idx=1"
        )
    with pytest.raises(IntegrityError):
        s.replay("r")
    s.close()


def test_lowered_horizon_tamper_classified_as_integrity(tmp_path):
    """Shrinking the frozen horizon below the decided count is an integrity
    fault during audit, not a RESOURCE_EXHAUSTED error."""
    from app.storage import RunStore

    db = tmp_path / "h.db"
    s = RunStore(str(db))
    s.create_run(LordConfig.create(alpha=0.05, w0=0.045, horizon=5), run_id="r")
    _drive(s, "r", [0.5, 0.5, 0.5])
    with s._conn:  # noqa: SLF001 - corrupt frozen horizon
        s._conn.execute("UPDATE runs SET horizon=1 WHERE run_id='r'")
    with pytest.raises(IntegrityError):
        s.replay("r")
    s.close()
