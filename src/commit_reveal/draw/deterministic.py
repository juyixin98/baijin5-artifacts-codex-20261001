"""Deterministic draw: map a seed to a ranking of eligible participants.

Fisher-Yates shuffle driven by the SHAKE256 draw stream, using rejection
sampling on 8-byte words so every permutation is (negligibly close to)
uniform — no modulo bias. The same seed and the same eligible set always
produce the same ranking; the winner is ``ranking[0]``.
"""

from __future__ import annotations

from commit_reveal.crypto import seed as seed_mod

_WORD = 8  # bytes per draw
_U64 = 1 << 64


def _next_index(stream: bytes, offset: int, bound: int) -> tuple[int, int]:
    """Draw an unbiased index in [0, bound) from the stream."""
    limit = _U64 - (_U64 % bound)
    while True:
        if offset + _WORD > len(stream):
            raise ValueError("draw stream exhausted; request a larger stream")
        candidate = int.from_bytes(stream[offset : offset + _WORD], "big")
        offset += _WORD
        if candidate < limit:
            return candidate % bound, offset


def deterministic_ranking(eligible: list[str], seed: bytes) -> list[str]:
    """Return a deterministic permutation of ``eligible`` given ``seed``."""
    items = sorted(eligible)
    n = len(items)
    if n == 0:
        raise ValueError("cannot draw from an empty eligible set")
    # Upper bound on stream bytes: n draws, each retried only with
    # probability < 1/2, so 16 words per draw is a generous cap.
    stream = seed_mod.draw_stream(seed, max(1, n) * _WORD * 16)
    offset = 0
    for i in range(n - 1, 0, -1):
        j, offset = _next_index(stream, offset, i + 1)
        items[i], items[j] = items[j], items[i]
    return items
