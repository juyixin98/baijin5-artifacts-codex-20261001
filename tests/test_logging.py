"""Log correlation tests: run identity, versions, decision basis."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.logging_setup import JsonFormatter
from tests.conftest import fixture_b64


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings().validate()))


def _fields(record) -> dict:
    return getattr(record, "fields", {}) or {}


def test_carve_logs_carry_run_identity(client, captured_logs):
    resp = client.post(
        "/v1/carve",
        json={"image_b64": fixture_b64("step_5x6.png"), "num_seams": 2},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    run_id = data["run_id"]

    started = captured_logs.events("run_started")
    assert started, "expected a run_started log record"
    started_fields = _fields(started[-1])
    assert started_fields["run_id"] == run_id
    assert started_fields["input_sha256"] == data["input_sha256"]
    versions = started_fields["versions"]
    assert "numpy" in versions and "scipy" in versions and "pillow" in versions

    seam_events = [
        r for r in captured_logs.events("seam_selected")
        if _fields(r).get("run_id") == run_id
    ]
    assert len(seam_events) == 2
    for index, record in enumerate(seam_events):
        fields = _fields(record)
        # Decision basis: energy, mode, displacement, tie count are logged.
        assert fields["seam_index"] == index
        assert fields["energy"] == 0.0
        assert fields["energy_mode"] == "gradient"
        assert fields["max_displacement"] == 1
        assert fields["input_sha256"] == data["input_sha256"]


def test_rejection_is_logged_with_category(client, captured_logs):
    resp = client.post(
        "/v1/seam",
        json={
            "image_b64": fixture_b64("step_5x6.png"),
            "protect_mask_b64": fixture_b64("mask_row_5x6.png"),
        },
    )
    assert resp.status_code == 422
    rejected = captured_logs.events("request_rejected")
    assert rejected
    fields = _fields(rejected[-1])
    assert fields["category"] == "NO_LEGAL_SEAM"
    assert fields["run_id"] == resp.json()["error"]["run_id"]


def test_log_records_are_json_parseable(client, captured_logs):
    client.get("/health")
    client.post("/v1/seam", json={"image_b64": fixture_b64("flat_4x4.png")})
    formatter = JsonFormatter()
    assert captured_logs.records, "expected captured log records"
    for record in captured_logs.records:
        payload = json.loads(formatter.format(record))
        assert payload["event"]
        assert payload["level"] in ("DEBUG", "INFO", "WARNING", "ERROR")
