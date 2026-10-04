"""SQLite provenance store: round-trip and idempotency lookups."""

from __future__ import annotations

import pytest


def _record(run_id="run1", request_id="req1", input_hash="hash1"):
    return {
        "run_id": run_id,
        "request_id": request_id,
        "input_hash": input_hash,
        "params": {"negative_branch_mode": "allow"},
        "status": "ok",
        "newick": "(L0:1,L1:1);",
        "leaf_map": {"L0": "a", "L1": "b"},
        "residuals": {"sum_abs": 0.0},
        "events": [{"type": "q_tie", "round": 0}],
    }


def test_save_and_get_roundtrip(store):
    store.save_run(_record())
    record = store.get_run("run1")
    assert record is not None
    assert record["run_id"] == "run1"
    assert record["params"] == {"negative_branch_mode": "allow"}
    assert record["leaf_map"] == {"L0": "a", "L1": "b"}
    assert record["residuals"] == {"sum_abs": 0.0}
    assert record["events"] == [{"type": "q_tie", "round": 0}]
    assert record["created_at"]  # timestamp recorded for replay/audit


def test_get_unknown_run_returns_none(store):
    assert store.get_run("nope") is None


def test_find_by_request_id(store):
    store.save_run(_record())
    found = store.find_by_request_id("req1")
    assert found is not None and found["run_id"] == "run1"
    assert store.find_by_request_id("other") is None


def test_request_id_is_unique(store):
    store.save_run(_record())
    with pytest.raises(Exception):
        store.save_run(_record(run_id="run2"))  # same request_id


def test_failed_run_record_persists_error(store):
    record = _record(request_id=None)
    record["status"] = "error"
    record["newick"] = None
    record["error"] = {"category": "COMPUTATION_FAILED", "message": "boom"}
    store.save_run(record)
    fetched = store.get_run("run1")
    assert fetched["status"] == "error"
    assert fetched["error"]["category"] == "COMPUTATION_FAILED"
    assert fetched["newick"] is None
