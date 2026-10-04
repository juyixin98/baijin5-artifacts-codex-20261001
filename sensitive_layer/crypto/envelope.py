"""Randomized authenticated encryption: AES-256-GCM (cryptography package).

Every encryption uses a fresh random 96-bit nonce, so identical plaintexts
produce unrelated ciphertexts. Layout: nonce (12B) || ciphertext || GCM tag.
The key version travels in the database column next to the blob, not in the
blob itself.
"""
from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LEN = 12
_TAG_LEN = 16


class DecryptionError(Exception):
    """Authentication tag mismatch or malformed blob."""


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ct


def decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < NONCE_LEN + _TAG_LEN:
        raise DecryptionError("ciphertext too short")
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise DecryptionError("authentication failed") from exc
