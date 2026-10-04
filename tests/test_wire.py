"""Strict wire-grammar tests: ordering, duplicates, encoding, GS2, CB scope."""
from __future__ import annotations

import base64

import pytest

from scram_auth import wire
from scram_auth.errors import (
    InvalidEncoding,
    ProtocolViolation,
    UnsupportedChannelBinding,
    UnsupportedMechanism,
)

MAX_ATTR = 8


class TestGs2Header:
    def test_parses_non_plus_n_and_y(self) -> None:
        gs2, bare = wire.parse_gs2_header("n,,n=alice,r=abc")
        assert gs2.cb_flag == "n" and gs2.authzid is None and bare == "n=alice,r=abc"
        gs2, bare = wire.parse_gs2_header("y,,n=alice,r=abc")
        assert gs2.cb_flag == "y"

    def test_parses_authzid_field(self) -> None:
        gs2, _ = wire.parse_gs2_header("n,a=bob,n=alice,r=abc")
        assert gs2.authzid == "bob"

    @pytest.mark.parametrize("bad", ["n,n=alice,r=abc", "z,,n=alice,r=abc", "n,", "n,x=y,r=abc"])
    def test_rejects_malformed_headers(self, bad: str) -> None:
        with pytest.raises(ProtocolViolation):
            wire.parse_gs2_header(bad)

    def test_plus_header_accepts_only_tls_server_end_point(self) -> None:
        gs2, _ = wire.parse_gs2_header("p=tls-server-end-point,,n=alice,r=abc")
        assert gs2.is_plus and gs2.cb_type == "tls-server-end-point"
        assert gs2.serialise() == b"p=tls-server-end-point,,"

    def test_plus_header_rejects_tls_unique(self) -> None:
        with pytest.raises(UnsupportedChannelBinding) as exc:
            wire.parse_gs2_header("p=tls-unique,,n=alice,r=abc")
        assert exc.value.category.value == "unsupported-channel-binding"


class TestAttributeGrammar:
    def test_client_first_requires_exact_n_then_r(self) -> None:
        parsed = wire.parse_client_first("n,,n=alice,r=abc1234567890123", max_attributes=MAX_ATTR)
        assert parsed.username == "alice" and parsed.client_nonce == "abc1234567890123"

    def test_rejects_reversed_order(self) -> None:
        with pytest.raises(ProtocolViolation) as exc:
            wire.parse_client_first("n,,r=abc1234567890123,n=alice", max_attributes=MAX_ATTR)
        assert exc.value.detail["attribute"] == "r"

    def test_rejects_duplicate_attribute(self) -> None:
        with pytest.raises(ProtocolViolation) as exc:
            wire.parse_client_first("n,,n=alice,n=bob,r=abc1234567890123", max_attributes=MAX_ATTR)
        assert "duplicate" in str(exc.value) and exc.value.detail["attribute"] == "n"

    def test_rejects_unknown_and_mandatory_extension(self) -> None:
        with pytest.raises(UnsupportedMechanism):
            wire.parse_client_first("n,,m=evil,n=alice,r=abc1234567890123", max_attributes=MAX_ATTR)
        with pytest.raises(ProtocolViolation):
            wire.parse_client_first("n,,n=alice,x=y,r=abc1234567890123", max_attributes=MAX_ATTR)

    def test_rejects_missing_required_attribute(self) -> None:
        with pytest.raises(ProtocolViolation) as exc:
            wire.parse_client_first("n,,n=alice", max_attributes=MAX_ATTR)
        assert exc.value.detail["missing"] == ["r"]

    @pytest.mark.parametrize(
        "message",
        [
            "n,,n=alice,r=ab,c",        # raw comma inside nonce value
            "n,,n=alice,r=",            # empty nonce
            "n,,r=abc1234567890123",    # missing username
        ],
    )
    def test_rejects_bad_values(self, message: str) -> None:
        with pytest.raises(ProtocolViolation):
            wire.parse_client_first(message, max_attributes=MAX_ATTR)

    def test_attribute_count_cap(self) -> None:
        with pytest.raises(ProtocolViolation) as exc:
            wire.parse_server_first("r=a,s=AA==,i=4096,x=1", max_attributes=2)
        assert exc.value.detail["limit"] == 2


class TestBase64AndNumbers:
    def test_base64_validator_rejects_stray_chars(self) -> None:
        with pytest.raises(InvalidEncoding):
            wire.b64_decode("AA!!", "s")

    @pytest.mark.parametrize("bad_i", ["01", "-1", "1e3", "abc", ""])
    def test_server_first_iteration_must_be_canonical_positive_int(self, bad_i: str) -> None:
        with pytest.raises(ProtocolViolation):
            wire.parse_server_first(f"r=abc1234567890123,s=AA==,i={bad_i}", max_attributes=MAX_ATTR)

    def test_server_first_roundtrip(self) -> None:
        msg = wire.build_server_first("abc" + "srvfrag", b"\x00\x01", 4096)
        parsed = wire.parse_server_first(msg, max_attributes=MAX_ATTR)
        assert parsed.nonce.endswith("srvfrag") and parsed.salt == b"\x00\x01" and parsed.iteration_count == 4096


class TestUsernameEscaping:
    @pytest.mark.parametrize(
        "wire_name,expected",
        [("alice", "alice"), ("a=2Cb", "a,b"), ("eq=3Dd", "eq=d"), ("mix=2C=3Dx", "mix,=x")],
    )
    def test_unescape_roundtrip(self, wire_name: str, expected: str) -> None:
        assert wire.unescape_username(wire_name) == expected
        assert wire.escape_username(expected) == wire_name

    def test_invalid_escape_sequence(self) -> None:
        with pytest.raises(ProtocolViolation):
            wire.unescape_username("bad=4A")


class TestChannelBindingValue:
    def test_non_plus_value_is_gs2_header_only(self) -> None:
        gs2, _ = wire.parse_gs2_header("n,,n=alice,r=a")
        assert wire.channel_binding_value(gs2, None) == b"n,,"
        assert base64.b64encode(b"n,,").decode() == "biws"  # RFC 7677 c= value

    def test_plus_value_concatenates_cert_hash(self) -> None:
        gs2, _ = wire.parse_gs2_header("p=tls-server-end-point,,n=alice,r=a")
        digest = b"\x11" * 32
        value = wire.channel_binding_value(gs2, digest)
        assert value == b"p=tls-server-end-point,," + digest

    def test_plus_without_hash_is_mismatch(self) -> None:
        gs2, _ = wire.parse_gs2_header("p=tls-server-end-point,,n=alice,r=a")
        with pytest.raises(Exception) as exc:
            wire.channel_binding_value(gs2, None)
        assert exc.value.category.value == "channel-bindings-dont-match"

    def test_non_plus_rejects_surplus_hash(self) -> None:
        gs2, _ = wire.parse_gs2_header("n,,n=alice,r=a")
        with pytest.raises(Exception) as exc:
            wire.channel_binding_value(gs2, b"\x11" * 32)
        assert exc.value.category.value == "channel-bindings-dont-match"
