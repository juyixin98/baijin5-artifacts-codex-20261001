"""Canonical, unambiguous byte encoding for protocol messages.

Every hashed structure is encoded as a sequence of length-prefixed fields:
each field is ``len(field)`` as 2-byte big-endian followed by the raw bytes.
Domain-separation tags are versioned so a future protocol change cannot
collide with v1 transcripts.
"""

from __future__ import annotations

DOMAIN_COMMITMENT = b"CRP-COMMIT-v1"
DOMAIN_SEED = b"CRP-SEED-v1"
DOMAIN_SET = b"CRP-SET-v1"

VALUE_LEN = 32  # bytes, random value revealed by a participant
SALT_LEN = 32  # bytes, random salt hiding the value before reveal
COMMITMENT_HEX_LEN = 64  # sha256 hexdigest


def _field(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError("field too long for u16 length prefix")
    return len(data).to_bytes(2, "big") + data


def commitment_preimage(
    round_id: str, participant_id: str, value: bytes, salt: bytes
) -> bytes:
    """Canonical preimage bound to (round, participant, value, salt)."""
    if len(value) != VALUE_LEN:
        raise ValueError(f"value must be {VALUE_LEN} bytes, got {len(value)}")
    if len(salt) != SALT_LEN:
        raise ValueError(f"salt must be {SALT_LEN} bytes, got {len(salt)}")
    return b"".join(
        _field(part)
        for part in (
            DOMAIN_COMMITMENT,
            round_id.encode("utf-8"),
            participant_id.encode("utf-8"),
            value,
            salt,
        )
    )


def commitment_set_preimage(entries: list[tuple[str, str]]) -> bytes:
    """Canonical preimage of the frozen commitment set.

    ``entries`` is a list of (participant_id, commitment_hex); it is sorted
    here so callers cannot influence the hash through ordering.
    """
    parts = [_field(DOMAIN_SET)]
    for participant_id, commitment_hex in sorted(entries):
        parts.append(_field(participant_id.encode("utf-8")))
        parts.append(_field(commitment_hex.encode("ascii")))
    return b"".join(parts)


def seed_master(values: list[bytes]) -> bytes:
    """Combine revealed values into the HKDF master secret.

    Values are sorted lexicographically so the result is independent of
    reveal arrival order. Each value is length-prefixed to avoid
    concatenation ambiguity.
    """
    if not values:
        raise ValueError("cannot derive a seed from zero revealed values")
    return b"".join(_field(v) for v in sorted(values))
