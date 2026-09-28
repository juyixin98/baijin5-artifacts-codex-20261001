"""SQLite run registry tests: state machine, error categories, replay log."""
from __future__ import annotations

import pytest

from aipw.contract import ErrorCategory, RunState, StateConflictError
from aipw.jobs import JobStore


def test_lifecycle_pending_running_succeeded():
    with JobStore(":memory:") as store:
        rid = store.create_run(seed=5)
        assert store.get(rid)["state"] == RunState.PENDING.value
        store.mark_running(rid, {"folds": 5})
        assert store.get(rid)["state"] == RunState.RUNNING.value
        result = {"point": 0.5, "se": 0.1, "ci_lower": 0.3, "ci_upper": 0.7,
                  "n": 100, "folds": 5, "fold_diagnostics": [],
                  "independent_units": 100, "trimmed_fraction": 0.0,
                  "gcomp_point": 0.4, "ipw_point": 0.6, "aipw_point": 0.5}
        store.mark_succeeded(rid, result)
        record = store.get(rid)
        assert record["state"] == RunState.SUCCEEDED.value
        assert record["result"]["point"] == 0.5
        assert record["error"] is None


def test_run_id_reuse_is_state_conflict():
    with JobStore(":memory:") as store:
        store.create_run(seed=1, run_id="fixed-id")
        with pytest.raises(StateConflictError) as exc:
            store.create_run(seed=2, run_id="fixed-id")
        assert exc.value.category is ErrorCategory.STATE
        assert "already exists" in exc.value.message


def test_illegal_transition_pending_to_succeeded_rejected():
    with JobStore(":memory:") as store:
        rid = store.create_run(seed=0)
        with pytest.raises(StateConflictError, match="illegal"):
            store.mark_succeeded(rid, {"point": 1.0})


def test_terminal_run_cannot_be_failed_again():
    with JobStore(":memory:") as store:
        rid = store.create_run(seed=0)
        store.mark_failed(rid, ErrorCategory.INPUT.value, "bad", {})
        with pytest.raises(StateConflictError, match="terminal"):
            store.mark_failed(rid, ErrorCategory.INPUT.value, "again", {})


def test_failed_run_records_distinct_category():
    with JobStore(":memory:") as store:
        rid = store.create_run(seed=0)
        store.mark_running(rid, {})
        store.mark_failed(rid, ErrorCategory.COMPUTATION.value,
                          "IRLS diverged", {"iteration": 200})
        record = store.get(rid)
        assert record["state"] == RunState.FAILED.value
        assert record["error"]["category"] == ErrorCategory.COMPUTATION.value
        assert record["error"]["details"]["iteration"] == 200


def test_events_are_sequential_and_replayable():
    with JobStore(":memory:") as store:
        rid = store.create_run(seed=9)
        store.mark_running(rid, {"n": 100})
        store.mark_succeeded(rid, {
            "point": 0.42, "se": 0.02, "ci_lower": 0.38, "ci_upper": 0.46,
            "n": 100, "folds": 5, "fold_diagnostics": [{"valid_size": 20}],
            "independent_units": 100, "trimmed_fraction": 0.0,
            "gcomp_point": 0.4, "ipw_point": 0.44, "aipw_point": 0.42})
        events = store.events(rid)
        assert [e["seq"] for e in events] == [0, 1, 2]
        assert [e["state"] for e in events] == [
            RunState.PENDING.value, RunState.RUNNING.value,
            RunState.SUCCEEDED.value]
        # replay-critical intermediate state is retained on the success event
        summary = events[2]["detail"]
        assert summary["point"] == 0.42
        assert summary["fold_sizes"] == [20]
        assert summary["components"]["aipw"] == 0.42
        # pending event records the seed so the split can be regenerated
        assert events[0]["detail"]["seed"] == 9


def test_events_for_unknown_run_is_state_conflict():
    with JobStore(":memory:") as store:
        with pytest.raises(StateConflictError, match="unknown run id"):
            store.events("nope")


def test_persistence_across_connections(tmp_path):
    db = tmp_path / "runs.db"
    with JobStore(db) as store:
        store.create_run(seed=3, run_id="persist-me")
    with JobStore(db) as store2:
        assert "persist-me" in store2.list_run_ids()
