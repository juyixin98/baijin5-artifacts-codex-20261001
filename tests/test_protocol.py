"""协议编码层测试：规范化、规范编码、信封。"""
import pytest

from blindex import protocol
from blindex.errors import BlindIndexError, Category


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Alice@Example.com", "alice@example.com"),
            ("  ALICE@example.COM ", "alice@example.com"),
            ("bob@example.org", "bob@example.org"),
        ],
    )
    def test_email(self, raw, expected):
        assert protocol.normalize("email", raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+1 (555) 010-2030", "+15550102030"),
            ("+1-555-010-2030", "+15550102030"),
            ("15550102030", "15550102030"),
            (" +86 138 0000 1111 ", "+8613800001111"),
        ],
    )
    def test_phone(self, raw, expected):
        assert protocol.normalize("phone", raw) == expected

    def test_name_whitespace_and_case(self):
        assert protocol.normalize("name", "  Alice   Smith ") == "alice smith"

    def test_id_number(self):
        assert protocol.normalize("id_number", "ab-123 cd") == "AB123CD"

    def test_null_passthrough(self):
        assert protocol.normalize("email", None) is None

    def test_unknown_field_category(self):
        with pytest.raises(BlindIndexError) as ei:
            protocol.normalize("ssn", "x")
        assert ei.value.category is Category.VALIDATION_ERROR

    def test_non_string_category(self):
        with pytest.raises(BlindIndexError) as ei:
            protocol.normalize("email", 123)
        assert ei.value.category is Category.VALIDATION_ERROR


class TestCanonicalEncoding:
    def test_length_prefix_avoids_concat_ambiguity(self):
        a = protocol.canonical_index_input("d", "ab", "c")
        b = protocol.canonical_index_input("d", "a", "bc")
        assert a != b

    def test_domain_separation(self):
        a = protocol.canonical_index_input("domain-a", "email", "x@y.z")
        b = protocol.canonical_index_input("domain-b", "email", "x@y.z")
        assert a != b

    def test_field_separation(self):
        a = protocol.canonical_index_input("d", "email", "v")
        b = protocol.canonical_index_input("d", "phone", "v")
        assert a != b

    def test_empty_domain_rejected(self):
        with pytest.raises(BlindIndexError) as ei:
            protocol.canonical_index_input("", "email", "v")
        assert ei.value.category is Category.CONFIG_ERROR


class TestEnvelope:
    def test_roundtrip(self):
        blob = protocol.encode_envelope(7, b"n" * 12, b"ct-and-tag")
        version, nonce, ct = protocol.decode_envelope(blob)
        assert (version, nonce, ct) == (7, b"n" * 12, b"ct-and-tag")

    @pytest.mark.parametrize(
        "blob",
        [
            b"",                          # 空
            b"CE1",                       # 太短
            b"XX1" + b"\x00" * 30,        # 魔数错误
            b"CE1" + b"\x00" * 10,        # 无 tag 空间
        ],
    )
    def test_malformed_category(self, blob):
        with pytest.raises(BlindIndexError) as ei:
            protocol.decode_envelope(blob)
        assert ei.value.category is Category.ENVELOPE_MALFORMED
