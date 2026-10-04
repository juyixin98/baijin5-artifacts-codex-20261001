"""Unit tests for protocol framing, nonce derivation and AAD encoding."""

from __future__ import annotations

import struct

import pytest

from app.core.errors import ProtocolCodingError
from app.core.protocol import (
    FLAG_FINAL, MAGIC, PROTOCOL_VERSION, TAG_LEN, decode_frame, derive_nonce,
    encode_aad, encode_frame,
)

pytestmark = pytest.mark.unit


def test_frame_roundtrip_covers_all_header_fields():
    ct = b"\xab" * (TAG_LEN + 5)
    frame = encode_frame("msg-1", 7, 9, 1234, True, ct)
    parsed = decode_frame(frame)

    assert parsed.version == PROTOCOL_VERSION
    assert parsed.message_id == b"msg-1"
    assert parsed.seqno == 7
    assert parsed.total_segments == 9
    assert parsed.total_len == 1234
    assert parsed.is_final is True
    assert parsed.ciphertext == ct
    assert parsed.plaintext_len == 5


def test_nonce_is_unique_per_slot_identity_and_terminator(fixture_keys):
    k = fixture_keys.nonce_key
    n0 = derive_nonce(k, "msg-a", 0, False)
    n1 = derive_nonce(k, "msg-a", 1, False)
    nf = derive_nonce(k, "msg-a", 0, True)   # same index, final marker differs
    other = derive_nonce(k, "msg-b", 0, False)

    assert len({n0, n1, nf, other}) == 4
    assert len(n0) == 12


def test_nonce_derivation_is_deterministic_for_retries(fixture_keys):
    k = fixture_keys.nonce_key
    assert derive_nonce(k, "msg-a", 3, False) == derive_nonce(k, "msg-a", 3, False)


def test_aad_changes_with_every_binding_field():
    base = dict(message_id="m", seqno=1, total_segments=4, is_final=False,
                total_len=100, segment_plaintext_len=25)
    baseline = encode_aad(**base)
    for field_name, new_value in (
        ("message_id", "n"),
        ("seqno", 2),
        ("total_segments", 5),
        ("is_final", True),
        ("total_len", 101),
        ("segment_plaintext_len", 26),
    ):
        mutated = dict(base)
        mutated[field_name] = new_value
        assert encode_aad(**mutated) != baseline, field_name


@pytest.mark.parametrize("damage", [
    b"",
    b"short",
    b"X" * 40,
    MAGIC + b"\x09",                       # bad version
])
def test_decode_rejects_malformed_frames(damage):
    with pytest.raises(ProtocolCodingError) as ei:
        decode_frame(damage)
    assert ei.value.category == "protocoding"


def test_decode_rejects_seqno_beyond_total():
    frame = encode_frame("m", 5, 3, 10, False, b"\x00" * TAG_LEN)
    with pytest.raises(ProtocolCodingError) as ei:
        decode_frame(frame)
    assert ei.value.category == "protocoding"


def test_decode_rejects_trailing_bytes_and_bad_flags():
    ct = b"\x00" * TAG_LEN
    frame = encode_frame("m", 0, 1, 0, True, ct) + b"\x00"
    with pytest.raises(ProtocolCodingError):
        decode_frame(frame)

    # Flip a reserved flag bit.
    raw = bytearray(encode_frame("m", 0, 1, 0, True, ct))
    # flags byte sits right before ciphertext_len (offset = len-4-ctlen)
    flags_at = len(raw) - 4 - len(ct)
    raw[flags_at] = FLAG_FINAL | 0x02
    with pytest.raises(ProtocolCodingError) as ei:
        decode_frame(bytes(raw))
    assert ei.value.category == "protocoding"


def test_message_id_length_bounds():
    with pytest.raises(ProtocolCodingError):
        encode_frame("", 0, 1, 0, True, b"\x00" * TAG_LEN)
    with pytest.raises(ProtocolCodingError):
        encode_frame("x" * 129, 0, 1, 0, True, b"\x00" * TAG_LEN)


def test_struct_layout_is_big_endian_and_stable():
    # Guards against accidental wire-format drift.
    frame = encode_frame("m", 1, 2, 256, False, b"c" * TAG_LEN)
    assert frame[:8] == MAGIC
    assert frame[8] == PROTOCOL_VERSION
    assert struct.unpack(">H", frame[9:11])[0] == 1
