"""Chunked job tests: progress reporting and run-identity logging."""

from __future__ import annotations

import json

import numpy as np
import pytest

from seamcarve.config import SeamConfig
from seamcarve.errors import NoLegalSeamError
from seamcarve.jobs import CarveJob


def test_chunked_progress_sequence(fixture_loader, tmp_path, run_logger):
    image, _, descriptor = fixture_loader("random_12x10")
    log_file = tmp_path / "job.log"
    job = CarveJob(
        image,
        np.zeros(image.shape[:2], bool),
        n_seams=5,
        config=SeamConfig(energy_mode="gradient", chunk_size=2),
        job_id="job-test-001",
        log_path=log_file,
    )
    report = job.run()

    assert [c["seams_done"] for c in job.progress] == [2, 4, 5]
    assert [c["current_width"] for c in job.progress] == [8, 6, 5]
    assert report.final_width == 5

    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert all(r["run_id"] == "job-test-001" for r in records)
    start = records[0]
    assert start["event"] == "job_start"
    assert start["input_sha256"] == descriptor["sha256"]
    assert "numpy" in start["versions"] and "seamcarve" in start["versions"]
    events = [r["event"] for r in records]
    assert events.count("chunk_done") == 3
    assert events[-1] == "job_done"
    run_logger.emit(
        "assertion", step="job", basis="chunk progress + log correlation",
        job_id="job-test-001", events=events,
    )


def test_failed_job_logs_failure_not_success(fixture_loader, tmp_path):
    image, mask, _ = fixture_loader("protected_row_6x6")
    log_file = tmp_path / "job-fail.log"
    job = CarveJob(
        image, mask, n_seams=1,
        config=SeamConfig(energy_mode="gradient"),
        job_id="job-test-fail",
        log_path=log_file,
    )
    with pytest.raises(NoLegalSeamError):
        job.run()
    events = [json.loads(line)["event"] for line in log_file.read_text().splitlines()]
    assert events == ["job_start", "job_failed"]
