"""SQLite storage tests."""
from __future__ import annotations

import json

from app.storage import RunStore


def test_save_get_list_roundtrip(tmp_path):
    store = RunStore(tmp_path / "r.db")
    req = {"x": [1, 2], "cutoff": 0}
    result = {"run_id": "r1", "status": "success", "failure_category": None,
              "kernel": "triangular", "bandwidth_method": "rot",
              "diagnostics": {"sample": {"n": 2}}, "estimate": {"tau": 1.0}}
    store.save_run(req, result)
    got = store.get_run("r1")
    assert got["run_id"] == "r1" and got["estimate"]["tau"] == 1.0
    rows = store.list_runs()
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["n"] == 2
    assert store.get_run("missing") is None


def test_request_and_result_are_stored_verbatim(tmp_path):
    store = RunStore(tmp_path / "r2.db")
    req = {"x": [1.5], "note": "keep-me"}
    res = {"run_id": "r2", "status": "warning", "failure_category": None,
           "kernel": "uniform", "bandwidth_method": "fixed",
           "diagnostics": {"sample": {"n": 1}}, "estimate": {}}
    store.save_run(req, res)
    import sqlite3
    con = sqlite3.connect(tmp_path / "r2.db")
    row = con.execute(
        "SELECT request_json FROM runs WHERE run_id='r2'").fetchone()
    con.close()
    assert json.loads(row[0])["note"] == "keep-me"
