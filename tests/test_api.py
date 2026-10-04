"""API 层测试：请求身份关联、结果可解释、失败类别单列。"""
import pytest
from fastapi.testclient import TestClient

from blindex.api import create_app
from blindex.audit import AuditLog
from blindex.crypto_adapter import CryptoAdapter
from blindex.service import BlindIndexService
from blindex.verify import IndependentVerifier

from reference import FIXTURES


@pytest.fixture()
def client(storage, keyring):
    audit = AuditLog(storage)
    service = BlindIndexService(storage, CryptoAdapter(keyring), audit)
    verifier = IndependentVerifier(
        keyring.domain, keyring.index_bits, keyring.index_keys
    )
    app = create_app(service, storage, audit, verifier)
    return TestClient(app)


class TestApi:
    def test_create_query_roundtrip_with_request_identity(self, client):
        r = client.post("/records", json={"fields": FIXTURES["fx-alice"]})
        assert r.status_code == 201
        rid = r.json()["record_id"]
        req_id = r.headers["X-Request-Id"]
        assert r.json()["request_id"] == req_id

        r = client.post(
            "/query",
            json={"field": "email", "value": " ALICE@example.COM"},
            headers={"X-Request-Id": "req-explicit-1"},
        )
        body = r.json()
        assert r.headers["X-Request-Id"] == "req-explicit-1"
        assert body["request_id"] == "req-explicit-1"
        assert body["matched_record_ids"] == [rid]
        assert body["searched_index_versions"] == [1]
        assert body["uncertain"] == []

    def test_null_query_category_via_api(self, client):
        r = client.post("/query", json={"field": "email", "value": None})
        body = r.json()
        assert body["status"] == "rejected"
        assert body["category"] == "NULL_QUERY"

    def test_unknown_field_category_via_api(self, client):
        r = client.post("/query", json={"field": "ssn", "value": "x"})
        assert r.status_code == 400
        assert r.json()["category"] == "VALIDATION_ERROR"

    def test_not_found_category_via_api(self, client):
        r = client.get("/records/does-not-exist")
        assert r.status_code == 404
        assert r.json()["category"] == "NOT_FOUND"

    def test_rotation_endpoints(self, client):
        client.post("/records", json={"fields": FIXTURES["fx-alice"]})
        client.post("/records", json={"fields": FIXTURES["fx-bob"]})

        r = client.post("/admin/rotate-index-key")
        assert r.json()["query_versions"] == [1, 2]

        # 中断式分批重建
        while True:
            out = client.post("/admin/reindex", json={"limit": 1}).json()
            q = client.post(
                "/query", json={"field": "email", "value": "alice@example.com"}
            ).json()
            assert len(q["matched_record_ids"]) == 1  # 任何时刻不漏
            if out["state"] == "idle":
                break

        status = client.get("/admin/rotation-status").json()
        assert status["state"] == "idle"
        assert status["query_versions"] == [2]
        assert status["index_versions_present"] == [2]

    def test_audit_endpoint_has_no_plaintext(self, client):
        client.post("/records", json={"fields": FIXTURES["fx-alice"]})
        client.post("/query", json={"field": "email", "value": "alice@example.com"})
        entries = client.get("/audit").json()["entries"]
        assert entries
        assert "alice@example.com" not in str(entries)

    def test_verify_check_endpoint(self, client):
        created = {}
        for fx_id, fields in FIXTURES.items():
            r = client.post("/records", json={"fields": fields})
            created[fx_id] = r.json()["record_id"]

        client.put(
            "/verify/fixtures",
            json={"fixtures": {created[k]: v for k, v in FIXTURES.items()}},
        )
        r = client.post(
            "/verify/check", json={"field": "email", "value": "ALICE@example.com"}
        )
        report = r.json()
        assert report["ok"] is True
        assert report["expected"] == sorted(
            [created["fx-alice"], created["fx-alice-alias"]]
        )
        assert "不是匿名化" in report["note"]
