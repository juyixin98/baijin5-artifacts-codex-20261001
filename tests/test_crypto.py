"""Cryptographic primitive tests against RFC 7677 vectors and the stdlib oracle."""
from __future__ import annotations

import base64

import pytest

from scram_auth import crypto

from ._oracle_stdlib import derive_all


def test_salted_password_matches_rfc_and_three_independent_paths(rfc_vector) -> None:
    salt = base64.b64decode(rfc_vector["salt_b64"])
    # Production path: PyCryptodome.
    salted = crypto.derive_salted_password(b"pencil", salt, 4096)
    assert salted.hex() == rfc_vector["intermediate_hex"]["SaltedPassword"]
    # Independent path: stdlib oracle.
    oracle = derive_all(
        "pencil",
        salt,
        4096,
        rfc_vector["client_first_bare"],
        rfc_vector["server_first"],
        rfc_vector["client_final_without_proof"],
    )
    assert oracle.salted_password == salted


@pytest.mark.parametrize(
    "key_name,fn",
    [
        ("ClientKey", lambda sp: crypto.client_key(sp)),
        ("ServerKey", lambda sp: crypto.server_key(sp)),
    ],
)
def test_key_derivation_matches_rfc_vectors(rfc_vector, key_name, fn) -> None:
    salted = bytes.fromhex(rfc_vector["intermediate_hex"]["SaltedPassword"])
    assert fn(salted).hex() == rfc_vector["intermediate_hex"][key_name]


def test_full_key_chain_matches_oracle(rfc_vector) -> None:
    salt = base64.b64decode(rfc_vector["salt_b64"])
    salted = crypto.derive_salted_password(b"pencil", salt, 4096)
    ck = crypto.client_key(salted)
    sk = crypto.stored_key_from_client_key(ck)
    srvk = crypto.server_key(salted)
    auth_message = rfc_vector["auth_message"].encode("utf-8")
    csig = crypto.client_signature(sk, auth_message)
    proof = crypto.make_client_proof(ck, csig)
    ssig = crypto.server_signature(srvk, auth_message)

    expected = rfc_vector["intermediate_hex"]
    assert sk.hex() == expected["StoredKey"]
    assert csig.hex() == expected["ClientSignature"]
    assert proof.hex() == expected["ClientProof"]
    assert ssig.hex() == expected["ServerSignature"]

    # Wire encodings from RFC 7677 Appendix 3.
    assert base64.b64encode(proof).decode() == rfc_vector["client_proof_b64"]
    assert base64.b64encode(ssig).decode() == rfc_vector["server_signature_b64"]

    # Recovery identity used server-side.
    assert crypto.recover_client_key(proof, csig) == ck


def test_xor_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        crypto.xor_bytes(b"a", b"ab")


def test_random_nonce_is_printable_ascii_without_comma() -> None:
    nonce = crypto.random_nonce(24)
    assert len(nonce) == 24 and "," not in nonce
    assert all(0x21 <= ord(c) <= 0x7E for c in nonce)
    # Two draws differ with overwhelming probability; assert distinct.
    assert crypto.random_nonce(24) != nonce


def test_pbkdf2_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        crypto.pbkdf2_hmac_sha256(b"pw", b"salt", 0)
    with pytest.raises(TypeError):
        crypto.pbkdf2_hmac_sha256("pw", b"salt", 1)
