"""Protocol encoding tests.

Expected values are either hand-computed byte strings or the public
RFC 5869 HKDF test vector - never produced by the code under test.
"""

from __future__ import annotations

import pytest

from sae import protocol


def test_aad_golden_encoding():
    msg_id = bytes.fromhex("aa" * 16)
    aad = protocol.encode_aad(msg_id, seq=0x01020304, final=True, pt_len=5)
    expected = (
        b"SAE1"
        + bytes.fromhex("aa" * 16)
        + bytes.fromhex("0000000001020304")
        + b"\x01"
        + bytes.fromhex("0000000000000005")
    )
    assert aad == expected
    assert len(aad) == protocol.AAD_LEN == 37


def test_aad_final_flag_bit():
    base = protocol.encode_aad(b"\x00" * 16, 0, False, 0)
    fin = protocol.encode_aad(b"\x00" * 16, 0, True, 0)
    assert base[28] == 0x00
    assert fin[28] == 0x01
    assert base[:28] == fin[:28] and base[29:] == fin[29:]


def test_aad_decode_roundtrip():
    msg_id = bytes(range(16))
    decoded = protocol.decode_aad(protocol.encode_aad(msg_id, 42, True, 1000))
    assert decoded == {"msg_id": msg_id, "seq": 42, "final": True, "pt_len": 1000}


def test_aad_rejects_bad_inputs():
    with pytest.raises(ValueError):
        protocol.encode_aad(b"\x00" * 15, 0, False, 0)  # short msg_id
    with pytest.raises(ValueError):
        protocol.encode_aad(b"\x00" * 16, -1, False, 0)
    with pytest.raises(ValueError):
        protocol.encode_aad(b"\x00" * 16, protocol.MAX_SEQ + 1, False, 0)
    with pytest.raises(ValueError):
        protocol.decode_aad(b"too short")
    with pytest.raises(ValueError):
        protocol.decode_aad(b"XXXX" + b"\x00" * 33)  # bad magic


def test_nonce_golden_derivation():
    nonce = protocol.derive_nonce(bytes.fromhex("0102030405060708"), 0xAABBCCDD)
    assert nonce == bytes.fromhex("0102030405060708aabbccdd")
    assert len(nonce) == protocol.NONCE_LEN == 12


def test_nonce_distinct_per_seq():
    base = b"\xff" * 8
    nonces = {protocol.derive_nonce(base, s) for s in range(1000)}
    assert len(nonces) == 1000


def test_hkdf_rfc5869_case1():
    # RFC 5869, Test Case 1 (SHA-256) - public reference answer.
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    okm = protocol.hkdf_sha256(ikm, salt, info, 42)
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a"
        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
        "34007208d5b887185865"
    )


def test_message_key_binds_message_id():
    master = bytes(range(32))
    salt = b"\x11" * 16
    k1 = protocol.derive_message_key(master, salt, b"\x01" * 16)
    k2 = protocol.derive_message_key(master, salt, b"\x02" * 16)
    k3 = protocol.derive_message_key(master, b"\x12" * 16, b"\x01" * 16)
    assert len(k1) == 32
    assert k1 != k2, "key must depend on message id"
    assert k1 != k3, "key must depend on salt"


def test_split_ciphertext():
    blob = b"cipher" + b"\x00" * 16
    ct, tag = protocol.split_ciphertext(blob)
    assert ct == b"cipher" and tag == b"\x00" * 16
    with pytest.raises(ValueError):
        protocol.split_ciphertext(b"\x00" * 15)
