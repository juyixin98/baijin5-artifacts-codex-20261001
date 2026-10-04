"""Commitment and seed hashing, built on the `cryptography` package.

Commitment scheme:
    commitment = SHA-256(COMMIT_DOMAIN
                         || enc(round_id)
                         || enc(participant_id)
                         || enc(random_value)
                         || enc(salt))

Binding the round_id and participant_id into the digest means a commitment
made for one round or one participant cannot be replayed in another context.
The salt is a second secret so the random value itself may be low-entropy
without making the commitment dictionary-attackable before the reveal phase.

Seed combination:
    seed = SHA-256(SEED_DOMAIN || enc(round_id)
                   || enc(pid_1) || enc(value_1) || ... (sorted by pid))

Only *validly revealed* values enter the seed; unrevealed commitments are
excluded (see README for the resulting bias discussion).
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes

from commit_reveal.protocol.encoding import (
    COMMIT_DOMAIN,
    SEED_DOMAIN,
    encode_fields,
    encode_text,
)


def sha256(data: bytes) -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize()


def sha256_hex(data: bytes) -> str:
    return sha256(data).hex()


def compute_commitment(
    round_id: str,
    participant_id: str,
    random_value: bytes,
    salt: bytes,
) -> str:
    """Return the hex commitment binding (round, participant, value, salt)."""
    payload = COMMIT_DOMAIN + encode_fields(
        encode_text(round_id),
        encode_text(participant_id),
        random_value,
        salt,
    )
    return sha256_hex(payload)


def combine_seed(round_id: str, reveals: list[tuple[str, bytes]]) -> str:
    """Combine revealed random values into the round seed (hex).

    ``reveals`` is a list of ``(participant_id, random_value)`` pairs; it is
    sorted by participant id here so the seed is independent of arrival order.
    """
    if not reveals:
        raise ValueError("cannot derive a seed from zero reveals")
    fields: list[bytes] = [encode_text(round_id)]
    for participant_id, random_value in sorted(reveals, key=lambda r: r[0]):
        fields.append(encode_text(participant_id))
        fields.append(random_value)
    return sha256_hex(SEED_DOMAIN + encode_fields(*fields))
