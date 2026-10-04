"""Control-plane error contract: the four error categories map to
distinct HTTP statuses and distinct audit categories, and every record
carries the run id needed to replay the run."""
from __future__ import annotations

import struct

import pytest
from fastapi.testclient import TestClient

from trustlab.control import create_app
from trustlab.service import ServiceConfig, TrustLabService

from .conftest import connect_client, wait_audit


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def test_input_error_maps_to_400(client, fixtures):
    resp = client.post("/bundles", json={"roots_pem": ["not a pem at all"]})
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["category"] == "INPUT_ERROR"

    resp = client.post("/bundles", json={"roots_pem": []})
    assert resp.status_code == 422  # pydantic schema validation


def test_state_conflict_maps_to_409(client, fixtures):
    resp = client.post("/bundles", json={
        "roots_pem": [fixtures.ca_new.cert_pem],
        "explicit_version": 1,
    })
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "STATE_CONFLICT"

    resp = client.post("/rollback", json={"to_version": 999})
    assert resp.status_code == 400  # unknown target: input error


def test_bundle_lifecycle_over_http(client, fixtures):
    resp = client.post("/rotation/begin",
                       json={"new_roots_pem": [fixtures.ca_new.cert_pem]})
    assert resp.status_code == 201
    assert resp.json()["bundle"]["version"] == 2
    assert resp.json()["bundle"]["kind"] == "rotation_overlap"

    resp = client.post("/rotation/end",
                       json={"new_roots_pem": [fixtures.ca_new.cert_pem]})
    assert resp.status_code == 201
    assert resp.json()["bundle"]["version"] == 3

    resp = client.post("/rollback", json={"to_version": 1})
    assert resp.status_code == 201
    bundle = resp.json()["bundle"]
    assert bundle["version"] == 4
    assert bundle["kind"] == "rollback"

    resp = client.get("/bundles")
    versions = [b["version"] for b in resp.json()["bundles"]]
    assert versions == [1, 2, 3, 4]


def test_run_id_and_audit_replay(client, service):
    resp = client.get("/run")
    assert resp.json()["run_id"] == service.run_id

    resp = client.get("/audit")
    rows = resp.json()["audit"]
    assert rows, "audit trail must not be empty"
    assert all(row["run_id"] == service.run_id for row in rows)
    assert all(row["reasoning"] for row in rows)

    resp = client.get("/audit", params={"category": "STATE_CHANGE"})
    assert all(row["category"] == "STATE_CHANGE"
               for row in resp.json()["audit"])


def test_error_categories_are_distinguishable(client, service, fixtures,
                                              tmp_path):
    # INPUT_ERROR
    client.post("/bundles", json={"roots_pem": ["garbage"]})
    # STATE_CONFLICT
    client.post("/bundles", json={"roots_pem": [fixtures.ca_new.cert_pem],
                                  "explicit_version": 1})
    # RESOURCE_EXHAUSTED: oversized frame on an authenticated connection
    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    sess.sock.sendall(struct.pack(">I", 10_000_000) + b"x" * 64)
    wait_audit(service, category="RESOURCE_EXHAUSTED", event="frame_rejected")
    sess.close()
    # CRYPTO_FAILURE: untrusted client cert
    with pytest.raises(Exception):
        connect_client(service, fixtures, tmp_path, fixtures.client_b, "b")
    wait_audit(service, category="CRYPTO_FAILURE", event="handshake_failed")

    categories = {row["category"]
                  for row in service.store.list_audit(run_id=service.run_id)}
    assert {"INPUT_ERROR", "STATE_CONFLICT", "RESOURCE_EXHAUSTED",
            "CRYPTO_FAILURE"} <= categories


def test_connection_limit_is_resource_exhausted(fixtures, tmp_path):
    svc = TrustLabService(
        ServiceConfig(data_dir=tmp_path / "svc", max_connections=1),
        server_cert_pem=fixtures.server.cert_pem,
        server_key_pem=fixtures.server.key_pem,
        initial_roots_pem=[fixtures.ca_old.cert_pem],
    )
    svc.start()
    try:
        first = connect_client(svc, fixtures, tmp_path, fixtures.client_a, "a1")
        with pytest.raises(Exception):
            connect_client(svc, fixtures, tmp_path, fixtures.client_a, "a2")
        row = wait_audit(svc, category="RESOURCE_EXHAUSTED",
                         event="connection_rejected")
        assert row["detail"]["max_connections"] == 1
        first.close()
    finally:
        svc.stop()
