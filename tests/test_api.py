"""API tests: concrete payloads, concrete clusters, and HTTP status codes
mapped from the error taxonomy."""

import pytest
from fastapi.testclient import TestClient

from er_backend.api.main import create_app
from er_backend.config import Settings

from .fixtures import ORACLE_RECORDS


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "api.sqlite3"))
    with TestClient(app) as c:
        yield c


def _ingest_oracle(client):
    payload = {"records": [r.model_dump() for r in ORACLE_RECORDS]}
    resp = client.post("/records", json=payload)
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/constraints",
        json={"must_link": [["n3", "n4"]], "cannot_link": [["n1", "n4"]]},
    )
    assert resp.status_code == 200, resp.text


def test_resolve_returns_concrete_clusters_and_evidence(client):
    _ingest_oracle(client)
    resp = client.post("/resolve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    partition = sorted(sorted(c["record_ids"]) for c in body["clusters"])
    assert partition == [["n1", "n2"], ["n3", "n4"], ["n5"]]
    assert body["affected_record_ids"] == ["n1", "n2", "n3", "n4", "n5"]
    assert body["run_id"].startswith("run-")
    assert body["evidence"], "clusters must carry source evidence"

    # Run journal is retrievable and replayable.
    run = client.get(f"/runs/{body['run_id']}")
    assert run.status_code == 200
    assert run.json()["decisions"], "decision log must be persisted"
    replay = client.post(f"/runs/{body['run_id']}/replay")
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    latest = client.get("/clusters")
    assert latest.status_code == 200
    assert latest.json()["run_id"] == body["run_id"]


def test_invalid_record_payload_is_400_input_error(client):
    resp = client.post("/records", json={"records": [{"record_id": "x", "name": ""}]})
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_unknown_constraint_ids_are_400_input_error(client):
    _ingest_oracle(client)
    resp = client.post(
        "/constraints", json={"must_link": [["n1", "ghost"]], "cannot_link": []}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_conflicting_constraints_are_409_state_conflict(client):
    _ingest_oracle(client)
    resp = client.post(
        "/constraints",
        json={"must_link": [["n1", "n2"]], "cannot_link": [["n2", "n1"]]},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "state_conflict"


def test_lock_endpoint_locks_mapping(client):
    client.post(
        "/records",
        json={
            "records": [
                {"record_id": "P", "name": "Pine Labs"},
                {"record_id": "Q", "name": "Pine Laboratories"},
            ]
        },
    )
    resp = client.post("/locks", json={"record_ids": ["P", "Q"], "note": "ok"})
    assert resp.status_code == 200, resp.text
    resolved = client.post("/resolve").json()
    partition = sorted(sorted(c["record_ids"]) for c in resolved["clusters"])
    assert partition == [["P", "Q"]]


def test_resource_exhaustion_maps_to_413(tmp_path):
    app = create_app(
        db_path=str(tmp_path / "exhausted.sqlite3"),
        settings=Settings(max_candidates=0),
    )
    with TestClient(app) as c:
        c.post(
            "/records",
            json={
                "records": [
                    {"record_id": "A", "name": "Acme Trading"},
                    {"record_id": "B", "name": "Acme Trading Ltd"},
                ]
            },
        )
        resp = c.post("/resolve")
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_unknown_run_id_is_400(client):
    resp = client.get("/runs/run-does-not-exist")
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"
