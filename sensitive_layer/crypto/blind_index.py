"""Keyed blind index: HMAC-SHA256 (PyCryptodome), truncated to ``bits`` bits.

SECURITY NOTE: a deterministic blind index leaks equality between records —
anyone who can read the index column can tell which rows share a value, and
can dictionary-attack low-entropy values. It is a search aid, NOT an
anonymization mechanism and NOT a substitute for the encryption layer.
"""
from __future__ import annotations

from Crypto.Hash import HMAC, SHA256

MIN_BITS = 8
MAX_BITS = 256  # SHA-256 output size


def compute_index(key: bytes, message: bytes, bits: int) -> bytes:
    if not MIN_BITS <= bits <= MAX_BITS:
        raise ValueError(f"bits must be within [{MIN_BITS}, {MAX_BITS}]")
    digest = HMAC.new(key, msg=message, digestmod=SHA256).digest()
    nbytes = (bits + 7) // 8
    out = bytearray(digest[:nbytes])
    remainder = bits % 8
    if remainder:
        out[-1] &= (0xFF << (8 - remainder)) & 0xFF
    return bytes(out)
