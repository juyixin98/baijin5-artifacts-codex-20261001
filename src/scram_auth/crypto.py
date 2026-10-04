"""Cryptographic primitives for SCRAM-SHA-256 (RFC 5802 + RFC 7677).

Production derivation uses **PyCryptodome** (``Crypto.Protocol.KDF.PBKDF2``
and ``Crypto.Hash``).  The independent test oracle in
``tests/_oracle_stdlib.py`` re-derives the same values with the standard
library's ``hashlib``/``hmac`` (OpenSSL-backed), so agreement between the two
is a meaningful cross-implementation check rather than code reviewing itself.

Key derivation (H = SHA-256, i = iteration count)::

    SaltedPassword  = Hi(password, salt, i)          # PBKDF2-HMAC-SHA-256
    ClientKey       = HMAC(SaltedPassword, "Client Key")
    StoredKey       = H(ClientKey)
    ServerKey       = HMAC(SaltedPassword, "Server Key")
    AuthMessage     = client-first-bare || "," || server-first || "," || client-final-without-proof
    ClientSignature = HMAC(StoredKey, AuthMessage)
    ClientProof     = ClientKey XOR ClientSignature
    ServerSignature = HMAC(ServerKey, AuthMessage)
"""
from __future__ import annotations

import os

from Crypto.Hash import HMAC, SHA256  # nosec B413 - PyCryptodome (actively maintained PyCrypto successor), not legacy PyCrypto
from Crypto.Protocol.KDF import PBKDF2  # nosec B413 - PyCryptodome PBKDF2, pinned to pycryptodome==3.20.0

HASH_NAME = "SHA-256"
HASH_DIGEST_SIZE = SHA256.digest_size  # 32 bytes for SHA-256


def hash_sha256(data: bytes) -> bytes:
    """H(data) using SHA-256."""
    return SHA256.new(data).digest()


def hmac_sha256(key: bytes, message: bytes) -> bytes:
    """HMAC-SHA-256(key, message)."""
    return HMAC.new(key, message, SHA256).digest()


def pbkdf2_hmac_sha256(password: bytes, salt: bytes, iterations: int, dklen: int = HASH_DIGEST_SIZE) -> bytes:
    """PBKDF2 with HMAC-SHA-256 via PyCryptodome."""
    if iterations < 1:
        raise ValueError("iteration count must be >= 1")
    if not isinstance(password, bytes) or not isinstance(salt, bytes):
        raise TypeError("password and salt must be bytes")
    return PBKDF2(password, salt, dkLen=dklen, count=iterations, hmac_hash_module=SHA256)


def xor_bytes(left: bytes, right: bytes) -> bytes:
    if len(left) != len(right):
        raise ValueError(f"xor length mismatch: {len(left)} != {len(right)}")
    return bytes(a ^ b for a, b in zip(left, right))


def random_salt(length: int) -> bytes:
    """Cryptographically secure random salt (``os.urandom``)."""
    if length < 16:
        raise ValueError("salt length must be >= 16 bytes")
    return os.urandom(length)


def random_nonce(length: int) -> str:
    """Random printable-ASCII nonce fragment excluding the comma (RFC 5802 5.1)."""
    if length < 16:
        raise ValueError("nonce fragment must be >= 16 raw bytes")
    allowed = _NONCE_ALPHABET
    buf = os.urandom(length)
    return "".join(allowed[b % len(allowed)] for b in buf)


# Printable ASCII except ',' (0x2C); RFC 5802 allows U+0021..U+002B, U+002D..U+007E.
_NONCE_ALPHABET = "".join(chr(c) for c in range(0x21, 0x7F) if c != 0x2C)


# ---------------------------------------------------------------------------
# SCRAM key material
# ---------------------------------------------------------------------------

def derive_salted_password(password: bytes, salt: bytes, iterations: int) -> bytes:
    return pbkdf2_hmac_sha256(password, salt, iterations)


def client_key(salted_password: bytes) -> bytes:
    return hmac_sha256(salted_password, b"Client Key")


def stored_key_from_client_key(client_key_bytes: bytes) -> bytes:
    return hash_sha256(client_key_bytes)


def server_key(salted_password: bytes) -> bytes:
    return hmac_sha256(salted_password, b"Server Key")


def client_signature(stored_key_bytes: bytes, auth_message: bytes) -> bytes:
    return hmac_sha256(stored_key_bytes, auth_message)


def server_signature(server_key_bytes: bytes, auth_message: bytes) -> bytes:
    return hmac_sha256(server_key_bytes, auth_message)


def make_client_proof(client_key_bytes: bytes, client_sig: bytes) -> bytes:
    return xor_bytes(client_key_bytes, client_sig)


def recover_client_key(client_proof: bytes, client_sig: bytes) -> bytes:
    """Server side: ClientKey = ClientProof XOR ClientSignature."""
    return xor_bytes(client_proof, client_sig)
