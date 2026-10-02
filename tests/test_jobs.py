"""Job runner: staged chunked execution, progress events, honest failure
status, and log records correlated to job id and input hash."""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from watershed_backend.config import Settings
from watershed_backend.jobs import COMPLETED, FAILED, JobRunner, JobStore
from watershed_backend.jobs.store import STAGE_ORDER
from watershed_backend.logging_utils import get_logger
from tests.fixtures.synthetic import two_basin


def _request() -> dict:
    gradient, seeds, _, _ = two_basin()
    return {
        "gradient": gradient.tolist(),
        "seeds": [{"row": s.row, "col": s.col, "label": s.label} for s in seeds],
        "connectivity": 8,
    }


def _runner(settings: Settings | None = None) -> tuple[JobRunner, JobStore]:
    store = JobStore()
    settings = settings or Settings(progress_chunk_pixels=4)
    return JobRunner(store, settings, get_logger("INFO")), store


def test_job_completes_through_all_stages_in_order():
    runner, _ = _runner()
    record = runner.run(runner.submit(_request()).job_id)
    assert record.status == COMPLETED
    assert [s.name for s in record.stages] == list(STAGE_ORDER)
    assert all(s.duration_ms is not None for s in record.stages)
    assert record.error is None
    assert record.result is not None


def test_job_result_matches_kernel_reference():
    from tests.fixtures.synthetic import two_basin as fixture

    _, _, expected_labels, _ = fixture()
    runner, _ = _runner()
    record = runner.run(runner.submit(_request()).job_id)
    np.testing.assert_array_equal(
        np.array(record.result["labels"], dtype=np.int32), expected_labels
    )


def test_flood_progress_events_are_emitted_and_terminate_at_total():
    runner, _ = _runner()
    record = runner.run(runner.submit(_request()).job_id)
    assert record.progress, "expected chunked progress events"
    final = record.progress[-1]
    assert final["done_pixels"] == final["total_pixels"] == 25
    levels = [p["water_level"] for p in record.progress]
    assert levels == sorted(levels), "water level never decreases"


def test_failed_job_is_failed_not_success():
    runner, _ = _runner()
    bad = _request()
    bad["seeds"] = [
        {"row": 0, "col": 0, "label": 1},
        {"row": 0, "col": 0, "label": 2},  # conflict
    ]
    record = runner.run(runner.submit(bad).job_id)
    assert record.status == FAILED
    assert record.error["category"] == "SEED_CONFLICT"
    assert record.result is None


def test_input_fingerprint_is_stable_and_sensitive():
    runner, _ = _runner()
    first = runner.run(runner.submit(_request()).job_id)
    second = runner.run(runner.submit(_request()).job_id)
    assert first.input_sha256 == second.input_sha256
    changed = _request()
    changed["gradient"][0][0] = 999.0
    third = runner.run(runner.submit(changed).job_id)
    assert third.input_sha256 != first.input_sha256


def test_logs_carry_job_identity_and_versions():
    import io

    from watershed_backend.logging_utils import JsonFormatter

    logger = get_logger("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    try:
        store = JobStore()
        runner = JobRunner(store, Settings(), logger)
        record = runner.run(runner.submit(_request()).job_id)
    finally:
        logger.removeHandler(handler)

    events = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    assert events, "expected structured log records"
    assert all(e["job_id"] == record.job_id for e in events)
    validated = [e for e in events if e.get("event") == "input_validated"]
    assert validated, "expected an input_validated event"
    detail = validated[0]["detail"]
    assert "numpy" in detail["versions"]
    assert detail["seed_count"] == 2
    assert validated[0]["input_sha256"] == record.input_sha256
    completed = [e for e in events if e.get("event") == "job_completed"]
    assert completed and completed[0]["detail"]["stats"]["boundary_pixels"] == 5
