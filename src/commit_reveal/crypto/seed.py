"""Seed derivation and draw stream backed by PyCryptodome.

- Seed: HKDF-SHA256 (RFC 5869) over the sorted, length-prefixed revealed
  values, salted with the round id and bound to a versioned context string.
- Draw stream: SHAKE256 XOF over the seed, giving an unbounded deterministic
  byte stream for the Fisher-Yates shuffle in ``draw.deterministic``.
"""

from __future__ import annotations

from Crypto.Hash import SHA256, SHAKE256
from Crypto.Protocol.KDF import HKDF

from commit_reveal.protocol import encoding

SEED_LEN = 32  # bytes


def derive_seed(round_id: str, revealed_values: list[bytes]) -> bytes:
    master = encoding.seed_master(revealed_values)
    return HKDF(
        master=master,
        key_len=SEED_LEN,
        salt=round_id.encode("utf-8"),
        hashmod=SHA256,
        context=encoding.DOMAIN_SEED,
    )


def draw_stream(seed: bytes, nbytes: int) -> bytes:
    shake = SHAKE256.new(seed)
    return shake.read(nbytes)
