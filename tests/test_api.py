"""API tests: endpoints, HTTP status mapping of error categories."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kds.api import create_app
from kds.crypto_adapter import MAX_DERIVE_LEN

BASE = {"tenant": "tenant-alpha", "purpose": "encryption", "version": 1, "context": "nightly"}


@pytest.fixture()
def client(service, store):
    return TestClient(create_app(service, store))


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_derive_is_deterministic_over_http(client):
    r1 = client.post("/v1/keys/derive", json=BASE)
    r2 = client.post("/v1/keys/derive", json=BASE)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["key_hex"] == r2.json()["key_hex"]
    assert r1.json()["key_id"] == r2.json()["key_id"]
    assert r1.json()["run_id"] != r2.json()["run_id"]


def test_derive_purpose_separation_over_http(client):
    enc = client.post("/v1/keys/derive", json=BASE).json()
    sig = client.post("/v1/keys/derive", json={**BASE, "purpose": "signing"}).json()
    assert enc["key_hex"] != sig["key_hex"]


def test_input_error_maps_to_400(client):
    resp = client.post("/v1/keys/derive", json={**BASE, "tenant": "Bad Tenant"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "input"


def test_oversized_length_rejected_by_schema(client):
    resp = client.post("/v1/keys/derive", json={**BASE, "length": MAX_DERIVE_LEN + 1})
    # pydantic rejects out-of-range lengths with 422 before the service runs;
    # the service-level ceiling is covered separately in test_service.py.
    assert resp.status_code == 422


def test_quota_exhaustion_maps_to_413(root_key, tmp_path):
    from kds.service import DerivationTreeService
    from kds.state import StateStore

    store = StateStore(str(tmp_path / "q.sqlite3"), max_keys_per_tenant=1)
    svc = DerivationTreeService(root_key, store)
    c = TestClient(create_app(svc, store))
    first = {**BASE, "display_name": "key one"}
    assert c.post("/v1/keys/register", json=first).status_code == 201
    second = {**BASE, "context": "other", "display_name": "key two"}
    resp = c.post("/v1/keys/register", json=second)
    assert resp.status_code == 413
    assert resp.json()["error"] == "resource_exhausted"


def test_register_conflict_maps_to_409(client):
    body = {**BASE, "display_name": "nightly key"}
    assert client.post("/v1/keys/register", json=body).status_code == 201
    conflict = client.post(
        "/v1/keys/register", json={**BASE, "display_name": "renamed key"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "state_conflict"


def test_register_then_get_metadata(client):
    client.post("/v1/keys/register", json={**BASE, "display_name": "nightly key"})
    key_id = client.post("/v1/keys/derive", json=BASE).json()["key_id"]
    record = client.get(f"/v1/keys/{key_id}")
    assert record.status_code == 200
    assert record.json()["display_name"] == "nightly key"
    assert "key_hex" not in record.json()


def test_unknown_key_id_is_404(client):
    assert client.get("/v1/keys/kds1-" + "0" * 32).status_code == 404


def test_audit_endpoint_lists_runs(client):
    client.post("/v1/keys/derive", json=BASE)
    entries = client.get("/v1/audit").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["event"] == "derive"
    assert entries[0]["outcome"] == "ok"
