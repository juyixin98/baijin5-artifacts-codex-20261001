"""独立验证层与审计约束测试。"""
import pytest

from blindex.errors import BlindIndexError, Category

from reference import FIXTURES, ref_expected_matches

REQ = "t"


class TestIndependentVerifier:
    def test_index_agreement_with_core(self, service, verifier, keyring):
        """独立验证器（hashlib）与核心（PyCryptodome）索引值一致。"""
        for version in keyring.index_keys:
            got = verifier.expected_index_hex("email", "Alice@Example.com ", version)
            assert got == service._adapter.blind_index(
                "email", "alice@example.com", version
            )

    def test_check_query_reports_ok_and_drift(self, loaded_service, verifier):
        service, id_map = loaded_service
        fx_to_sys = id_map
        # 把夹具 id 换成系统 record_id 后再比对
        fixtures = {fx_to_sys[k]: v for k, v in FIXTURES.items()}

        result = service.query("email", "alice@example.com", REQ)
        report = verifier.check_query(
            fixtures, "email", "alice@example.com", result["matched_record_ids"]
        )
        assert report["ok"] is True
        assert report["missing"] == [] and report["unexpected"] == []

        # 人为制造漂移：漏报一条 → 验证器必须指出 missing
        drifted = result["matched_record_ids"][:-1]
        report = verifier.check_query(fixtures, "email", "alice@example.com", drifted)
        assert report["ok"] is False
        assert report["missing"] == [result["matched_record_ids"][-1]]

    def test_expected_matches_uses_plaintext_scan(self, verifier):
        expected = verifier.expected_matches(FIXTURES, "phone", "+1 555 010 2030")
        assert expected == ["fx-alice", "fx-alice-alias"]
        assert expected == ref_expected_matches(FIXTURES, "phone", "+1 555 010 2030")


class TestAuditConstraints:
    def test_audit_contains_only_record_identity(self, loaded_service, storage):
        service, id_map = loaded_service
        service.query("email", "alice@example.com", REQ)
        service.query("email", None, REQ)
        entries = storage.list_audit()
        assert entries, "审计日志不应为空"
        blob = str(entries)
        # 敏感值绝不出现在日志中
        for forbidden in ["alice@example.com", "Alice", "555", "bob@example.org"]:
            assert forbidden not in blob
        # 记录身份与请求身份必须在（夹具加载与查询各用各的请求身份）
        assert id_map["fx-alice"] in blob
        assert all(e["request_id"] for e in entries)
        query_entries = [e for e in entries if e["op"] == "query"]
        assert query_entries and all(e["request_id"] == REQ for e in query_entries)

    def test_audit_rejects_non_whitelisted_keys(self, service):
        with pytest.raises(BlindIndexError) as ei:
            service._audit.log("query", REQ, value="alice@example.com")
        assert ei.value.category is Category.VALIDATION_ERROR

    def test_query_audit_records_versions_and_counts(self, loaded_service, storage):
        service, _ = loaded_service
        service.query("email", "alice@example.com", REQ)
        entry = [e for e in storage.list_audit() if e["op"] == "query"][-1]
        assert entry["detail"]["versions"] == [1]
        assert entry["detail"]["confirmed"] == 2
        assert entry["detail"]["field"] == "email"
