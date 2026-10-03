"""Weighted Shannon entropy and information content per column."""

from __future__ import annotations

import math


def shannon_entropy_bits(distribution: dict[str, float]) -> float:
    """H = -sum(p * log2(p)) over non-zero probabilities."""
    entropy = 0.0
    for p in distribution.values():
        if p > 0.0:
            entropy -= p * math.log2(p)
    return entropy


def information_content_bits(distribution: dict[str, float], alphabet_size: int) -> float:
    """IC = log2(alphabet_size) - H. For DNA this tops out at 2 bits."""
    return math.log2(alphabet_size) - shannon_entropy_bits(distribution)
