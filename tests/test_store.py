"""Integration tests for SQLite persistence."""
from __future__ import annotations

import json

import pytest

from app.contract import RunStatus
from app.store import RunStore


def _make_payload(run_id: str, status: RunStatus, tau: float | None, n: int = 12):
    req = {"data": [{"x": float(i) / 10, "y": 1.0} for i in range(n)], "cutoff": 0.5}
    resp = {
        "run_id": run_id,
        "input_label": f"label-{run_id}",
        "status": status.value,
        "cutoff": 0.5,
        "estimate": None if tau is None else {"tau": tau},
    }
    return json.dumps(req), json.dumps(resp)


@pytest.mark.integration
def test_save_get_and_list_roundtrip() -> None:
    store = RunStore(":memory:")
    rq, rs = _make_payload("run-1", RunStatus.OK, 2.34)
    store.save(rq, rs)
    row = store.get("run-1")
    assert row is not None
    assert row["status"] == "ok"
    assert row["tau"] == pytest.approx(2.34)
    assert row["n_obs"] == 12
    assert json.loads(row["response_json"])["run_id"] == "run-1"

    rq2, rs2 = _make_payload("run-2", RunStatus.UNIDENTIFIED, None)
    store.save(rq2, rs2)
    assert len(store.list_runs()) == 2
    only_unid = store.list_runs(status="unidentified")
    assert [r["run_id"] for r in only_unid] == ["run-2"]


@pytest.mark.integration
def test_get_missing_returns_none() -> None:
    assert RunStore(":memory:").get("nope") is None


@pytest.mark.integration
def test_save_is_idempotent_on_same_run_id() -> None:
    store = RunStore(":memory:")
    rq, rs = _make_payload("run-x", RunStatus.OK, 1.0)
    store.save(rq, rs)
    store.save(rq, rs)  # replace, not duplicate
    assert len(store.list_runs()) == 1
