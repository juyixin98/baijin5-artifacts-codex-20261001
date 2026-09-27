"""End-to-end HTTP API tests via Starlette's TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.repository import Repository
from app.service import FimService
from tests.brute_force import closed_itemsets as oracle_closed
from tests.conftest import CORPUS_ABC, CORPUS_EMPTY_DUP


@pytest.fixture
def client(settings):
    service = FimService(Repository(settings.database_path), settings)
    app = create_app(service, log_level="WARNING")
    return TestClient(app)


def _create_dataset(client, rows, name=None, request_id="req-ingest"):
    response = client.post(
        "/datasets",
        json={"name": name, "transactions": rows},
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 201, response.text
    return response


def test_health_reports_versions_and_correlates(client):
    response = client.get("/health", headers={"X-Request-ID": "req-health"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["engine_version"]
    assert body["request_id"] == "req-health"
    assert response.headers["X-Request-ID"] == "req-health"
    assert response.headers["X-Engine-Version"]


def test_dataset_lifecycle_and_duplicate_identity_stats(client):
    response = _create_dataset(client, CORPUS_EMPTY_DUP)
    body = response.json()
    stats = body["stats"]
    assert stats["transaction_count"] == 4
    assert stats["empty_transaction_count"] == 2
    assert stats["duplicate_transaction_count"] == 4  # two identical pairs
    assert stats["distinct_item_count"] == 1  # repeated "x" counted once

    fetched = client.get(f"/datasets/{body['dataset_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["content_hash"] == body["content_hash"]


def test_invalid_corpus_returns_named_error_category(client):
    response = client.post(
        "/datasets",
        json={"transactions": [{"tid": "dup", "items": ["a"]}, {"tid": "dup", "items": ["b"]}]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_CORPUS"
    assert "duplicate tids" in body["message"]
    assert body["request_id"]
    assert body["engine_version"]


def test_schema_validation_error_is_distinct_category(client):
    # min_length=1 on transactions and min_support ge=1.
    response = client.post("/datasets", json={"transactions": []})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"

    _create_dataset(client, CORPUS_ABC)
    # Need a real dataset id first; check min_support=0 rejection explicitly.
    ds = _create_dataset(client, CORPUS_ABC).json()["dataset_id"]
    bad = client.post("/jobs", json={"dataset_id": ds, "min_support": 0})
    assert bad.status_code == 422
    assert bad.json()["error_code"] == "VALIDATION_ERROR"


def test_job_complete_matches_oracle_and_distinguishes_closed_vs_maximal(client):
    ds = _create_dataset(client, CORPUS_ABC).json()["dataset_id"]
    response = client.post(
        "/jobs", json={"dataset_id": ds, "min_support": 2}, headers={"X-Request-ID": "req-job"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["complete"] is True
    assert body["status"] == "complete"
    assert body["request_id"] == "req-job"
    assert body["maximal_results_certain"] is True

    got_closed = {frozenset(e["itemset"]): e["support"] for e in body["closed_itemsets"]}
    assert got_closed == oracle_closed(CORPUS_ABC, 2)
    got_max = {tuple(e["itemset"]) for e in body["maximal_itemsets"]}
    assert got_max == {("a", "b"), ("a", "c"), ("b", "c")}
    # The distinction must be observable in the response itself.
    assert len(body["closed_itemsets"]) > len(body["maximal_itemsets"])
    assert body["chunk"]["evaluations_in_chunk"] >= 1


def test_budget_partial_then_resume_loop_to_completion(client):
    ds = _create_dataset(client, CORPUS_ABC).json()["dataset_id"]
    create = client.post(
        "/jobs", json={"dataset_id": ds, "min_support": 1, "budget": 0}
    ).json()
    assert create["complete"] is False
    assert create["maximal_results_certain"] is False
    assert create["maximal_itemsets"] == []  # withheld while uncertain
    assert any("PARTIAL" in note for note in create["notes"])
    job_id = create["job_id"]

    seen = {frozenset(e["itemset"]) for e in create["closed_itemsets"]}
    for step in range(50):
        body = client.post(
            f"/jobs/{job_id}/resume", json={"budget": 2}
        ).json()
        current = {frozenset(e["itemset"]) for e in body["closed_itemsets"]}
        assert seen <= current  # monotonic, no losses
        seen = current
        if body["complete"]:
            break
    assert body["complete"] is True
    got = {frozenset(e["itemset"]): e["support"] for e in body["closed_itemsets"]}
    assert got == oracle_closed(CORPUS_ABC, 1)
    assert body["maximal_results_certain"] is True
    assert {tuple(e["itemset"]) for e in body["maximal_itemsets"]} == {("a", "b", "c")}

    # GET returns the same durable state.
    fetched = client.get(f"/jobs/{job_id}").json()
    assert fetched["complete"] is True
    assert fetched["closed_itemsets"] == body["closed_itemsets"]


def test_missing_resources_are_404_with_codes(client):
    missing_ds = client.post("/jobs", json={"dataset_id": "nope", "min_support": 1})
    assert missing_ds.status_code == 404
    assert missing_ds.json()["error_code"] == "DATASET_NOT_FOUND"

    missing_job = client.post("/jobs/x/resume", json={})
    assert missing_job.status_code == 404
    assert missing_job.json()["error_code"] == "JOB_NOT_FOUND"
