"""HTTP boundary tests: status codes map to error categories; responses
carry run_id; key material appears only in the derive response body."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from keytree.api import create_app
from keytree.crypto_adapter import MAX_OKM_LEN


@pytest.fixture()
def client(tmp_path, root_hex):
    app = create_app(tmp_path / "api.db", tmp_path / "api.log", root_hex=root_hex)
    with TestClient(app) as c:
        yield c


def test_derive_success_matches_golden_vector(client, golden_tree):
    vec = golden_tree[0]
    resp = client.post("/v1/derive", json={
        "tenant": vec["tenant"],
        "purpose": vec["purpose"],
        "version": vec["version"],
        "context_b64": base64.b64encode(bytes.fromhex(vec["context_hex"])).decode(),
        "length": vec["length"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"run_id", "key_id", "key_hex", "fingerprint", "length"}
    # exact result against the independently generated golden vector
    assert body["key_id"] == vec["key_id"]
    assert body["key_hex"] == vec["key_hex"]
    assert body["fingerprint"] == vec["fingerprint"]


def test_derive_deterministic_over_http(client):
    payload = {"tenant": "acme", "purpose": "encryption", "version": 1}
    a = client.post("/v1/derive", json=payload).json()
    b = client.post("/v1/derive", json=payload).json()
    assert a["key_hex"] == b["key_hex"]
    assert a["key_id"] == b["key_id"]
    assert a["run_id"] != b["run_id"]


def test_invalid_input_is_400(client):
    resp = client.post("/v1/derive", json={
        "tenant": "BAD TENANT", "purpose": "p", "version": 0,
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"
    assert "run_id" in resp.json()["error"]


def test_invalid_base64_context_is_400(client):
    resp = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "p", "version": 0,
        "context_b64": "!!!not-base64!!!",
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_oversize_length_is_413(client):
    resp = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "p", "version": 0,
        "length": MAX_OKM_LEN + 1,
    })
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_name_binding_conflict_is_409(client):
    a = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "encryption", "version": 1,
        "display_name": "prod",
    }).json()
    b = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "signing", "version": 1,
    }).json()
    resp = client.post("/v1/names", json={
        "display_name": "prod", "key_id": b["key_id"],
    })
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "state_conflict"
    # the original binding still resolves to the first key
    meta = client.get(f"/v1/keys/{a['key_id']}").json()
    assert meta["display_name"] == "prod"


def test_key_metadata_excludes_material(client):
    derived = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "p", "version": 0,
    }).json()
    meta = client.get(f"/v1/keys/{derived['key_id']}")
    assert meta.status_code == 200
    assert "key_hex" not in meta.json()
    assert derived["key_hex"] not in meta.text


def test_unknown_key_id_is_400(client):
    resp = client.get("/v1/keys/kdt1_" + "0" * 40)
    assert resp.status_code == 400


def test_audit_endpoint_returns_run_events(client):
    derived = client.post("/v1/derive", json={
        "tenant": "acme", "purpose": "p", "version": 0,
    }).json()
    resp = client.get(f"/v1/audit/{derived['run_id']}")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert events[0]["outcome"] == "success"
    assert derived["key_hex"] not in resp.text
