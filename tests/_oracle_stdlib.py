"""Independent reference oracle for the SCRAM-SHA-256 test-suite.

IMPORTANT: this oracle deliberately uses ONLY the Python standard library
(``hashlib`` / ``hmac`` / ``base64``) and none of the production package's
crypto code, which is built on PyCryptodome.  Test expectations therefore
come from a second implementation plus the frozen RFC 7677 vectors — not
from the code under test.

The oracle also implements the SCRAM derivation from scratch (it does not
import ``scram_auth.crypto``), so it doubles as a cross-implementation check.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass


def _hi(password: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations)


@dataclass(frozen=True)
class OracleKeys:
    salted_password: bytes
    client_key: bytes
    stored_key: bytes
    server_key: bytes
    client_signature: bytes
    client_proof: bytes
    server_signature: bytes


def derive_all(
    password: str | bytes,
    salt: bytes,
    iterations: int,
    client_first_bare: str,
    server_first: str,
    client_final_without_proof: str,
) -> OracleKeys:
    pw = password.encode("utf-8") if isinstance(password, str) else password
    salted = _hi(pw, salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    auth_message = (
        client_first_bare + "," + server_first + "," + client_final_without_proof
    ).encode("utf-8")
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
    server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    return OracleKeys(salted, client_key, stored_key, server_key, client_signature, client_proof, server_signature)


def verify_client_proof(stored_key: bytes, auth_message: bytes, proof: bytes) -> bool:
    """Server-side verification using only stdlib primitives."""
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    recovered_client_key = bytes(a ^ b for a, b in zip(proof, client_signature))
    candidate_stored = hashlib.sha256(recovered_client_key).digest()
    return hmac.compare_digest(candidate_stored, stored_key)


def expected_server_signature(server_key: bytes, auth_message: bytes) -> bytes:
    return hmac.new(server_key, auth_message, hashlib.sha256).digest()


def tamper(payload_b64: str) -> str:
    """Flip one bit in the decoded payload and re-encode to standard base64."""
    raw = bytearray(base64.b64decode(payload_b64))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")
