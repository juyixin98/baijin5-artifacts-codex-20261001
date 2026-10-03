"""Exact significance calibration for PWM scores.

For a motif of length k the score distribution under the declared background
is computed *exactly* by enumerating all 4^k possible words, scoring each
with the PWM and weighting it by its background probability
prod_i bg[word_i]. The p-value of a score t is the tail probability

    P(S >= t) = sum over words w with score(w) >= t of P_bg(w)

Equal scores are aggregated after rounding to ``score_round_decimals``
decimals (declared discretisation; see README). Motif length is capped so
this enumeration stays feasible — that cap is a deliberate trade-off of the
exact approach.

Multiple-testing correction across all evaluated windows of a scan is
provided as Bonferroni and Benjamini-Hochberg adjusted p-values.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.pwm import PWM

#: Tolerance used when comparing a query score against the aggregated grid.
_SCORE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ScoreDistribution:
    """Exact discrete distribution of PWM scores under the background model."""

    scores: np.ndarray  # unique scores, sorted ascending
    tail_probs: np.ndarray  # tail_probs[i] = P(S >= scores[i])
    n_words: int  # total words enumerated (4 ** k)

    @property
    def max_score(self) -> float:
        return float(self.scores[-1])

    @property
    def min_score(self) -> float:
        return float(self.scores[0])


def enumerate_score_distribution(pwm: PWM, score_round_decimals: int = 9) -> ScoreDistribution:
    """Enumerate all 4^k words and aggregate the exact score distribution."""
    bg = pwm.background_vector
    scores = pwm.log_odds[0].copy()
    probs = bg.copy()
    for pos in range(1, pwm.length):
        scores = (scores[:, None] + pwm.log_odds[pos][None, :]).ravel()
        probs = (probs[:, None] * bg[None, :]).ravel()

    n_words = int(scores.size)
    rounded = np.round(scores, decimals=score_round_decimals)
    unique, inverse = np.unique(rounded, return_inverse=True)
    weight = np.bincount(inverse, weights=probs, minlength=unique.size)
    tail = weight[::-1].cumsum()[::-1].copy()
    # Guard against tiny negative/overshoot from float accumulation.
    tail = np.clip(tail, 0.0, 1.0)
    return ScoreDistribution(scores=unique, tail_probs=tail, n_words=n_words)


def pvalue_at_least(dist: ScoreDistribution, score: float) -> float:
    """Exact P(S >= score) under the background model."""
    idx = int(np.searchsorted(dist.scores, score - _SCORE_TOLERANCE, side="left"))
    if idx >= dist.scores.size:
        return 0.0
    return float(dist.tail_probs[idx])


def threshold_for_pvalue(dist: ScoreDistribution, pvalue: float) -> float | None:
    """Smallest achievable score whose tail probability is <= ``pvalue``.

    Returns None when even the best possible word is not significant enough
    (declared as THRESHOLD_UNREACHABLE by the caller).
    """
    eligible = np.nonzero(dist.tail_probs <= pvalue)[0]
    if eligible.size == 0:
        return None
    return float(dist.scores[int(eligible[0])])


def bonferroni(pvalues: list[float], m: int) -> list[float]:
    """Bonferroni-adjusted p-values for m tested hypotheses."""
    if m <= 0:
        return [1.0 for _ in pvalues]
    return [min(1.0, p * m) for p in pvalues]


def benjamini_hochberg(pvalues: list[float], m: int) -> list[float]:
    """Benjamini-Hochberg adjusted p-values for m tested hypotheses."""
    n = len(pvalues)
    if n == 0 or m <= 0:
        return [1.0 for _ in pvalues]
    order = sorted(range(n), key=lambda i: pvalues[i])
    adjusted = [1.0] * n
    running = 1.0
    for rank_from_top, i in enumerate(reversed(order)):
        rank = n - rank_from_top  # 1-based rank in ascending order
        running = min(running, pvalues[i] * m / rank)
        adjusted[i] = min(1.0, running)
    return adjusted
