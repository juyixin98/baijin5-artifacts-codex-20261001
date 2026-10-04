"""Deterministic seed-driven draw, built on PyCryptodome.

The seed selects an index in ``range(n)`` via HMAC-SHA256 in counter mode
with 128-bit rejection sampling, so the result is unbiased for any ``n``
(no modulo bias) and fully deterministic: anyone holding the evidence can
recompute the same index.
"""

from __future__ import annotations

from Crypto.Hash import HMAC, SHA256

from commit_reveal.protocol.encoding import DRAW_DOMAIN

DRAW_ALGORITHM = "HMAC-SHA256-counter-rejection-v1"
_SAMPLE_BITS = 128
_SAMPLE_BYTES = _SAMPLE_BITS // 8


def _draw_block(seed: bytes, counter: int) -> bytes:
    mac = HMAC.new(seed, digestmod=SHA256)
    mac.update(DRAW_DOMAIN)
    mac.update(counter.to_bytes(4, "big"))
    return mac.digest()


def draw_index(seed_hex: str, n: int) -> int:
    """Map a seed to an unbiased index in ``range(n)``, deterministically."""
    if n <= 0:
        raise ValueError("cannot draw from an empty candidate set")
    seed = bytes.fromhex(seed_hex)
    space = 1 << _SAMPLE_BITS
    limit = space - (space % n)
    counter = 0
    while True:
        candidate = int.from_bytes(_draw_block(seed, counter)[:_SAMPLE_BYTES], "big")
        if candidate < limit:
            return candidate % n
        counter += 1
