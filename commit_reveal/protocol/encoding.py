"""Canonical encoding for protocol messages.

All hashed structures use length-prefixed concatenation so that field
boundaries are unambiguous: ``encode_fields(a, b)`` can never collide with
``encode_fields(ab)`` or ``encode_fields(a, b, "")``. Every hashed object is
additionally prefixed with a versioned domain-separation tag so a commitment
can never be reinterpreted as a seed or a draw input.

This module is the single source of truth for byte layout; both the service
and the independent verifier build on it, and the test suite pins it against
an independent hashlib-only recomputation.
"""

from __future__ import annotations

# Versioned domain-separation tags. Bumping the version changes every digest.
COMMIT_DOMAIN = b"CRP1-COMMIT-v1"
SEED_DOMAIN = b"CRP1-SEED-v1"
DRAW_DOMAIN = b"CRP1-DRAW-v1"

EVIDENCE_VERSION = "crp-evidence/1"


def encode_fields(*fields: bytes) -> bytes:
    """Length-prefix each field (4-byte big-endian) and concatenate."""
    out = bytearray()
    for field in fields:
        if not isinstance(field, (bytes, bytearray)):
            raise TypeError(f"expected bytes, got {type(field).__name__}")
        out += len(field).to_bytes(4, "big")
        out += field
    return bytes(out)


def encode_text(value: str) -> bytes:
    return value.encode("utf-8")
