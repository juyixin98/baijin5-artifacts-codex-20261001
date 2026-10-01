"""Logging contract tests: run attribution, steps, and honest terminal states.

We assert that:
- estimation logs are attributable to the exact input ``run_id``;
- the log stream shows progress steps (received -> completed) and versions;
- a failed/unknown statistical state is logged at ERROR, never as success.
"""
from __future__ import annotations

import logging

import numpy as np

from app.api.main import estimate
from app.api.schemas import RDEstimateRequest
from app.core.estimator import rd_estimate
from app.dgp import sharp_jump, sparse_boundary
from app.logging_setup import configure_logging


def test_core_emits_no_success_for_failed_state(run_id):
    # Direct estimator call: structured result must be unambiguous.
    d = sparse_boundary(seed=4)
    res = rd_estimate(d.x, d.y, bandwidth=0.1, run_id=run_id)
    assert res.status.value == "failed"
    assert res.failure_category is not None
    assert res.tau is None


def test_api_logs_are_attributable_and_honest(caplog_handler, run_id):
    logger = configure_logging()
    before = len(caplog_handler.records)

    d = sharp_jump(n=1500, tau=4.0, seed=17)
    req = RDEstimateRequest(x=d.x.tolist(), y=d.y.tolist(), cutoff=0.0,
                            bandwidth="rot", run_id=run_id)
    resp = estimate(req)
    assert resp.status_code == 200

    entries = caplog_handler.data_for(run_id)
    messages = [e["message"] for e in entries]
    assert any("request" in m for m in messages)
    assert any("complete" in m for m in messages)
    # Progress/calculation steps are present and attributable.
    steps = {e["data"].get("step") for e in entries}
    assert {"received", "completed"} <= steps
    # A successful run logs versions alongside the verdict.
    completed = [e for e in entries if e["data"].get("step") == "completed"][0]
    assert completed["data"]["status"] == "success"
    assert "numpy" in completed["data"]["versions"]
    assert len(caplog_handler.records) > before


def test_failed_run_logged_at_error_not_success(caplog_handler, run_id):
    d = sparse_boundary(seed=18)
    req = RDEstimateRequest(x=d.x.tolist(), y=d.y.tolist(), cutoff=0.0,
                            bandwidth=0.1, run_id=run_id)
    resp = estimate(req)
    assert resp.status_code == 422
    entries = caplog_handler.data_for(run_id)
    completed = [e for e in entries if e["data"].get("step") == "completed"]
    assert completed and completed[0]["level"] == "ERROR"
    assert completed[0]["data"]["status"] == "failed"
    assert completed[0]["data"]["failure_category"] == "non_identifiable"
    # No record may claim success for this run.
    assert not any(
        e["level"] == "INFO" and e["data"].get("status") == "success"
        for e in entries
    )


def test_logger_emits_session_metadata(caplog_handler):
    logger = configure_logging()
    logger.info("progress checkpoint", extra={"data": {"step": "calibration",
                                                       "bandwidth": 0.123}})
    meta = [r for r in caplog_handler.records
            if getattr(r, "data", {}).get("step") == "calibration"]
    assert meta and meta[0].data["bandwidth"] == 0.123


def test_jsonl_handler_writes_attributable_line(tmp_path, run_id):
    from app.logging_setup import JsonlHandler
    import json
    path = tmp_path / "rd.jsonl"
    handler = JsonlHandler(path)
    logger = configure_logging()
    logger.addHandler(handler)
    try:
        logger.info("persisted checkpoint",
                    extra={"data": {"run_id": run_id, "step": "completed",
                                    "status": "success"}})
    finally:
        logger.removeHandler(handler)
    lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    match = [ln for ln in lines if ln.get("data", {}).get("run_id") == run_id]
    assert match and match[0]["data"]["status"] == "success"
    assert "ts" in match[0] and match[0]["level"] == "INFO"
