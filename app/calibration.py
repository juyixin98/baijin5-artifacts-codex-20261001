"""Exact significance calibration by full enumeration of k-mer scores.

For a motif of length k we enumerate all 4^k k-mers, score each with the
PWM, and weight each by its probability under the declared background
model. The result is the exact null distribution of the score, from which
p-values and score thresholds are read off directly. No sampling, no
asymptotic approximation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import BASES, MAX_EXACT_MOTIF_LENGTH, BackgroundModel
from .errors import InvalidAlphaError, MotifScanError, MotifTooLongError
from .pwm import PWM

# Tolerance for "score >= threshold" comparisons against enumerated values.
_SCORE_TOL = 1e-9
# Rounding used to merge floating-point-identical enumerated scores.
_MERGE_DECIMALS = 12


def validate_alpha(alpha: float) -> None:
    if not (0.0 < alpha <= 1.0):
        raise InvalidAlphaError(
            f"alpha must be in (0, 1], got {alpha!r}",
            {"alpha": alpha},
        )


@dataclass(frozen=True)
class Threshold:
    requested_alpha: float
    achievable: bool
    # None when no score reaches the requested alpha (achievable=False);
    # the achieved tail probability is then 0.0 by convention.
    score: float | None
    achieved_alpha: float


@dataclass(frozen=True)
class ScoreDistribution:
    """Exact null distribution of PWM scores under the background model."""

    scores: tuple[float, ...]       # distinct scores, ascending
    tail_probs: tuple[float, ...]   # P(S >= scores[i]) under background
    motif_length: int

    @property
    def n_distinct(self) -> int:
        return len(self.scores)

    @property
    def min_score(self) -> float:
        return self.scores[0]

    @property
    def max_score(self) -> float:
        return self.scores[-1]

    def pvalue(self, score: float) -> float:
        """P(S >= score) under the declared background model."""
        import bisect

        i = bisect.bisect_left(self.scores, score - _SCORE_TOL)
        if i >= len(self.scores):
            return 0.0
        return self.tail_probs[i]

    def threshold_for_alpha(self, alpha: float) -> Threshold:
        """Smallest score whose tail probability does not exceed alpha."""
        validate_alpha(alpha)
        for score, tail in zip(self.scores, self.tail_probs):
            if tail <= alpha:
                return Threshold(
                    requested_alpha=alpha,
                    achievable=True,
                    score=score,
                    achieved_alpha=tail,
                )
        return Threshold(
            requested_alpha=alpha,
            achievable=False,
            score=None,
            achieved_alpha=0.0,
        )


def enumerate_score_distribution(pwm: PWM, background: BackgroundModel) -> ScoreDistribution:
    """Enumerate all 4^k k-mers and aggregate the exact score distribution."""
    k = pwm.length
    if k > MAX_EXACT_MOTIF_LENGTH:
        raise MotifTooLongError(
            f"motif length {k} exceeds the exact-calibration limit of "
            f"{MAX_EXACT_MOTIF_LENGTH} (4^{k} k-mers); exact enumeration "
            "is the declared calibration method and longer motifs are "
            "out of scope",
            {"motif_length": k, "max": MAX_EXACT_MOTIF_LENGTH},
        )

    n = 4 ** k
    idx = np.arange(n, dtype=np.int64)
    powers = 4 ** np.arange(k - 1, -1, -1, dtype=np.int64)
    digits = (idx[:, None] // powers[None, :]) % 4  # (n, k) base indices

    scores = pwm.log_odds[np.arange(k)[None, :], digits].sum(axis=1)
    bg = np.array([background.probs[b] for b in BASES], dtype=np.float64)
    weights = np.exp(np.log(bg)[digits].sum(axis=1))

    rounded = np.round(scores, decimals=_MERGE_DECIMALS)
    uniq, inverse = np.unique(rounded, return_inverse=True)
    agg = np.zeros(len(uniq), dtype=np.float64)
    np.add.at(agg, inverse, weights)

    total = float(agg.sum())
    if abs(total - 1.0) > 1e-9:
        # Defensive: weights must partition the background probability mass.
        raise MotifScanError(  # pragma: no cover - unreachable by construction
            f"enumerated weights sum to {total!r}, not 1.0", {"sum": total}
        )

    tail = np.cumsum(agg[::-1])[::-1].copy()
    return ScoreDistribution(
        scores=tuple(float(s) for s in uniq),
        tail_probs=tuple(float(t) for t in tail),
        motif_length=k,
    )
