"""Commitment hashing backed by the ``cryptography`` package.

The adapter is deliberately thin: all structure (domain tags, field order,
length prefixes) lives in ``protocol.encoding``; this module only owns the
hash primitive so the underlying library can be swapped in one place.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes

from commit_reveal.protocol import encoding


def sha256_hex(data: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize().hex()


def compute_commitment(
    round_id: str, participant_id: str, value: bytes, salt: bytes
) -> str:
    """C = SHA-256(encode(DOMAIN, round, participant, value, salt))."""
    return sha256_hex(
        encoding.commitment_preimage(round_id, participant_id, value, salt)
    )


def commitment_set_hash(entries: list[tuple[str, str]]) -> str:
    """Hash of the frozen commitment set, order-independent."""
    return sha256_hex(encoding.commitment_set_preimage(entries))


def constant_time_equal(a: str, b: str) -> bool:
    """Hex-digest comparison without early exit."""
    import hmac

    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))
