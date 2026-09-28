"""Observability tests: run correlation, versions, explicit verdicts."""

from __future__ import annotations

import json

from sparse_embeddings import __version__
from sparse_embeddings.observability import StructuredLogger, new_run_id


def test_run_ids_are_distinct():
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100


def test_log_line_has_versions_run_id_and_verdict(tmp_path):
    path = tmp_path / "log.jsonl"
    logger = StructuredLogger(path, stderr=False)
    logger.event(
        "unit_event",
        run_id="run-xyz",
        verdict="applied",
        detail={"a": 1},
    )
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["run_id"] == "run-xyz"
    assert record["verdict"] == "applied"
    assert record["event"] == "unit_event"
    assert record["versions"]["sparse_embeddings"] == __version__
    assert record["versions"]["numpy"]
    assert record["versions"]["python"]
    assert "ts" in record and "pid" in record


def test_rejected_and_success_are_distinct_verdicts(tmp_path):
    path = tmp_path / "log.jsonl"
    logger = StructuredLogger(path, stderr=False)
    logger.event("batch_applied", run_id="r1", verdict="applied")
    logger.event("batch_rejected", run_id="r2", verdict="rejected")
    logger.event("batch_skipped", run_id="r3", verdict="skipped")
    verdicts = [
        json.loads(line)["verdict"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert verdicts == ["applied", "rejected", "skipped"]
