"""HTTP surface: request identity, typed error categories, audit privacy."""
import json


def test_put_get_query_roundtrip(client):
    resp = client.put("/records/r1", json={
        "field": "email", "purpose": "lookup:email",
        "value": "  Alice@Example.COM "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["index_versions_written"] == [1]

    resp = client.get("/records/r1", params={
        "field": "email", "purpose": "lookup:email", "include_plaintext": True})
    assert resp.json()["result"]["value"] == "alice@example.com"

    resp = client.post("/query", json={
        "field": "email", "purpose": "lookup:email", "value": "alice@example.com"})
    assert resp.json()["result"]["confirmed"] == ["r1"]


def test_request_id_echoed(client):
    resp = client.put("/records/r1", headers={"X-Request-Id": "req-abc"},
                      json={"field": "email", "purpose": "lookup:email",
                            "value": "a@example.com"})
    assert resp.json()["request_id"] == "req-abc"
    assert resp.headers["x-request-id"] == "req-abc"


def test_unknown_purpose_category(client):
    resp = client.put("/records/r1", json={
        "field": "email", "purpose": "no:such", "value": "a@example.com"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["category"] == "unknown_purpose"
    assert body["request_id"]


def test_null_query_category(client):
    resp = client.post("/query", json={
        "field": "email", "purpose": "lookup:email", "value": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "null_not_indexable"


def test_missing_record_category(client):
    resp = client.get("/records/ghost", params={
        "field": "email", "purpose": "lookup:email"})
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "record_not_found"


def test_rotation_conflict_category(client):
    client.put("/records/r1", json={
        "field": "email", "purpose": "lookup:email", "value": "a@example.com"})
    resp = client.post("/admin/rotate-index-key", json={"crash_after": 0})
    assert resp.json()["result"]["rotation"]["status"] == "rotating"
    resp = client.post("/admin/rotate-index-key", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "rotation_conflict"
    resp = client.post("/admin/rotation/resume")
    assert resp.json()["result"]["rotation"]["status"] == "idle"


def test_audit_log_contains_record_identity_only(client, tmp_path):
    secret_value = "alice@example.com"
    client.put("/records/r1", json={
        "field": "email", "purpose": "lookup:email", "value": secret_value})
    client.post("/query", json={
        "field": "email", "purpose": "lookup:email", "value": secret_value})

    resp = client.get("/audit")
    entries = resp.json()["result"]["entries"]
    assert len(entries) >= 2

    blob = json.dumps(entries, ensure_ascii=False)
    # No value, normalized form, index digest or ciphertext may appear.
    assert secret_value not in blob
    assert "alice" not in blob.lower()

    allowed = {"ts", "request_id", "action", "record_id", "record_ids",
               "field", "purpose", "enc_key_version", "index_key_versions",
               "outcome", "counts", "reason"}
    for entry in entries:
        assert set(entry) <= allowed
    actions = {e["action"] for e in entries}
    assert {"put_record", "query"} <= actions
    query_entry = next(e for e in entries if e["action"] == "query")
    assert query_entry["record_ids"] == ["r1"]
    assert query_entry["index_key_versions"] == [1]
