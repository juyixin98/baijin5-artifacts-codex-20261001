"""Cross-implementation vector tests.

Expected ciphertexts come from tests/fixtures/vectors.json, generated
by scripts/make_fixtures.py with PyCryptodome.  Here the
``cryptography``-based core adapter must reproduce them bit-for-bit,
and both implementations must agree on accept/reject for tampered data.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from sae import protocol
from sae.crypto_aead import CoreAEAD, TagVerificationError
from sae.crypto_verify import IndependentVerifier

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "vectors.json").read_text()
)


def _cases():
    for msg in VECTORS["messages"]:
        for chunk in msg["chunks"]:
            yield msg, chunk


@pytest.mark.parametrize("msg,chunk", list(_cases()))
def test_core_matches_reference_ciphertext(msg, chunk):
    core = CoreAEAD()
    key = bytes.fromhex(msg["key_hex"])
    nonce = bytes.fromhex(chunk["nonce_hex"])
    aad = bytes.fromhex(chunk["aad_hex"])
    pt = base64.b64decode(chunk["plaintext_b64"])
    expected_ct = base64.b64decode(chunk["ciphertext_b64"])

    assert core.encrypt(key, nonce, aad, pt) == expected_ct
    assert core.decrypt(key, nonce, aad, expected_ct) == pt


@pytest.mark.parametrize("msg,chunk", list(_cases()))
def test_independent_verifier_accepts_reference(msg, chunk):
    verifier = IndependentVerifier()
    ok = verifier.verify(
        bytes.fromhex(msg["key_hex"]),
        bytes.fromhex(chunk["nonce_hex"]),
        bytes.fromhex(chunk["aad_hex"]),
        base64.b64decode(chunk["ciphertext_b64"]),
    )
    assert ok is True


def test_key_derivation_matches_fixture():
    # Fixture keys were derived via sae.protocol from the public spec;
    # recompute here and require equality with the stored values.
    master = bytes.fromhex(VECTORS["master_key_hex"])
    for msg in VECTORS["messages"]:
        key = protocol.derive_message_key(
            master,
            bytes.fromhex(msg["salt_hex"]),
            bytes.fromhex(msg["message_id"]),
        )
        assert key.hex() == msg["key_hex"]


def test_both_backends_reject_bit_flipped_tag():
    msg, chunk = VECTORS["messages"][0], VECTORS["messages"][0]["chunks"][0]
    key = bytes.fromhex(msg["key_hex"])
    nonce = bytes.fromhex(chunk["nonce_hex"])
    aad = bytes.fromhex(chunk["aad_hex"])
    blob = bytearray(base64.b64decode(chunk["ciphertext_b64"]))
    blob[-1] ^= 0x01  # flip one tag bit

    with pytest.raises(TagVerificationError):
        CoreAEAD().decrypt(key, nonce, aad, bytes(blob))
    assert IndependentVerifier().verify(key, nonce, aad, bytes(blob)) is False


def test_chunk_bound_to_seq_and_final_flag():
    # A ciphertext must not verify under a different seq or final flag.
    msg = VECTORS["messages"][0]
    chunk = msg["chunks"][1]  # non-final chunk
    key = bytes.fromhex(msg["key_hex"])
    aad = bytes.fromhex(chunk["aad_hex"])
    blob = base64.b64decode(chunk["ciphertext_b64"])
    verifier = IndependentVerifier()

    wrong_seq = protocol.derive_nonce(bytes.fromhex(msg["nonce_base_hex"]), 99)
    assert verifier.verify(key, wrong_seq, aad, blob) is False

    wrong_final = protocol.encode_aad(
        bytes.fromhex(msg["message_id"]), chunk["seq"], True,
        len(base64.b64decode(chunk["plaintext_b64"])),
    )
    assert verifier.verify(key, bytes.fromhex(chunk["nonce_hex"]), wrong_final, blob) is False
