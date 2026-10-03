"""Declared background model and global defaults.

The background base distribution is an explicit, validated object — every
score, p-value and threshold produced by this package is only meaningful
relative to one specific BackgroundModel.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import BackgroundNotNormalizedError, ZeroBackgroundProbabilityError

BASES: tuple[str, ...] = ("A", "C", "G", "T")
UNKNOWN_BASE = "N"

DEFAULT_BACKGROUND: dict[str, float] = {b: 0.25 for b in BASES}
DEFAULT_PSEUDOCOUNT: float = 1.0

# Exact calibration enumerates all 4^k k-mers; 4^10 ~ 1M rows is the
# practical ceiling we declare support for.
MAX_EXACT_MOTIF_LENGTH: int = 10

NORMALIZATION_TOLERANCE: float = 1e-6


@dataclass(frozen=True)
class BackgroundModel:
    """A validated background base distribution over A/C/G/T."""

    probs: dict[str, float]

    def __post_init__(self) -> None:
        keys = set(self.probs)
        if keys != set(BASES):
            raise BackgroundNotNormalizedError(
                "background must define exactly the bases A, C, G, T",
                {"received": sorted(keys)},
            )
        for base, p in self.probs.items():
            if p == 0.0:
                raise ZeroBackgroundProbabilityError(
                    f"background probability for base {base!r} is zero; "
                    "log-odds scores are undefined under a zero-probability "
                    "background — declare a positive pseudocount-like floor "
                    "in the background instead",
                    {"base": base},
                )
            if p < 0.0:
                raise BackgroundNotNormalizedError(
                    f"background probability for base {base!r} is negative",
                    {"base": base, "probability": p},
                )
        total = sum(self.probs.values())
        if abs(total - 1.0) > NORMALIZATION_TOLERANCE:
            raise BackgroundNotNormalizedError(
                f"background probabilities sum to {total!r}, not 1.0",
                {"sum": total, "tolerance": NORMALIZATION_TOLERANCE},
            )

    @classmethod
    def uniform(cls) -> "BackgroundModel":
        return cls(dict(DEFAULT_BACKGROUND))
