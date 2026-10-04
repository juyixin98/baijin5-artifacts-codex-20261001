"""查询语义测试：同值不同表示、强制短索引碰撞、NULL。"""
import pytest

from blindex.errors import BlindIndexError, Category

from reference import FIXTURES, ref_expected_matches

REQ = "t"


class TestSameValueDifferentRepresentation:
    def test_email_case_and_whitespace(self, loaded_service):
        service, id_map = loaded_service
        # 夹具里 fx-alice 与 fx-alice-alias 是同一邮箱的不同表示
        result = service.query("email", "  ALICE@Example.COM ", REQ)
        assert result["status"] == "ok"
        assert result["matched_record_ids"] == sorted(
            [id_map["fx-alice"], id_map["fx-alice-alias"]]
        )
        assert result["uncertain"] == []

    def test_phone_punctuation(self, loaded_service):
        service, id_map = loaded_service
        result = service.query("phone", "+1-555-010-2030", REQ)
        assert result["matched_record_ids"] == sorted(
            [id_map["fx-alice"], id_map["fx-alice-alias"]]
        )

    def test_name_casefold_and_whitespace(self, loaded_service):
        service, id_map = loaded_service
        result = service.query("name", "ALICE    smith", REQ)
        assert result["matched_record_ids"] == sorted(
            [id_map["fx-alice"], id_map["fx-alice-alias"]]
        )

    def test_no_match_returns_empty(self, loaded_service):
        service, _ = loaded_service
        result = service.query("email", "nobody@nowhere.test", REQ)
        assert result["status"] == "ok"
        assert result["matched_record_ids"] == []
        assert result["candidates"] == 0


class TestForcedCollision:
    """把索引截断到 4 bit，强制不同值共享同一盲索引。

    断言：候选包含碰撞双方，但解密二次确认后只返回真等值记录，
    且 rejected_candidates 精确计数。
    """

    @pytest.fixture()
    def tiny_service(self, storage, keyring):
        from blindex.audit import AuditLog
        from blindex.crypto_adapter import CryptoAdapter
        from blindex.service import BlindIndexService

        keyring.index_bits = 4  # 仅 16 个桶，碰撞必然且可构造
        return BlindIndexService(storage, CryptoAdapter(keyring), AuditLog(storage))

    def test_collision_filtered_by_decrypt_confirmation(self, tiny_service, keyring):
        from blindex.verify import IndependentVerifier

        # 用独立验证器(hashlib 路径)找到一对 4-bit 索引相同但规范化值不同的邮箱
        verifier = IndependentVerifier(keyring.domain, 4, keyring.index_keys)
        target = "coll-a@example.test"
        other = None
        target_idx = verifier.expected_index_hex("email", target, 1)
        for i in range(1000):
            cand = f"coll-b{i}@example.test"
            if verifier.expected_index_hex("email", cand, 1) == target_idx:
                other = cand
                break
        assert other is not None, "测试构造失败：未找到碰撞对"

        rid_a = tiny_service.create_record({"email": target}, REQ)
        rid_b = tiny_service.create_record({"email": other}, REQ)

        result = tiny_service.query("email", target, REQ)
        assert result["candidates"] == 2               # 碰撞双方都进了候选
        assert result["matched_record_ids"] == [rid_a]  # 只有真等值被确认
        assert result["rejected_candidates"] == 1       # 碰撞被解密确认排除
        assert rid_b not in result["matched_record_ids"]


class TestNullSemantics:
    def test_null_field_not_indexed(self, loaded_service, storage):
        service, id_map = loaded_service
        rid = id_map["fx-null-contact"]
        assert not storage.has_index("email", 1, rid)
        assert not storage.has_index("phone", 1, rid)
        assert storage.has_index("name", 1, rid)  # 非 NULL 字段仍入索引

    def test_null_query_rejected_with_category(self, loaded_service):
        service, _ = loaded_service
        result = service.query("email", None, REQ)
        assert result["status"] == "rejected"
        assert result["category"] == Category.NULL_QUERY.value
        assert result["matched_record_ids"] == []

    def test_null_record_not_returned_by_other_queries(self, loaded_service):
        service, id_map = loaded_service
        result = service.query("email", "bob@example.org", REQ)
        assert id_map["fx-null-contact"] not in result["matched_record_ids"]
        assert result["matched_record_ids"] == [id_map["fx-bob"]]

    def test_unknown_field_category(self, loaded_service):
        service, _ = loaded_service
        with pytest.raises(BlindIndexError) as ei:
            service.query("ssn", "x", REQ)
        assert ei.value.category is Category.VALIDATION_ERROR


class TestAgainstPlaintextReference:
    """所有夹具字段 × 表示变体，结果必须等于独立明文参考。"""

    QUERIES = [
        ("email", "Alice@Example.com"),
        ("email", "  alice@EXAMPLE.com"),
        ("email", "bob@example.org"),
        ("email", "absent@example.test"),
        ("phone", "+1 (555) 010-2030"),
        ("phone", "15550102030"),
        ("phone", "+8613800001111"),
        ("name", "alice smith"),
        ("name", "Bob  Jones"),
        ("name", "No Contact"),
        ("id_number", "ab123cd"),
        ("id_number", "AB-123 CD"),
        ("id_number", "zz 999"),
    ]

    @pytest.mark.parametrize("field,value", QUERIES)
    def test_matches_plaintext_reference(self, loaded_service, field, value):
        service, id_map = loaded_service
        result = service.query(field, value, REQ)
        expected_fx = ref_expected_matches(FIXTURES, field, value)
        expected_ids = sorted(id_map[fx] for fx in expected_fx)
        assert result["matched_record_ids"] == expected_ids
        assert result["uncertain"] == []
