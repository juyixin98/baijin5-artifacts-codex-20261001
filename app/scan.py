"""Strand-aware PWM scanning.

Every window of length k on both strands is evaluated:

* ``+`` strand: the window ``seq[i:i+k]`` scored directly;
* ``-`` strand: the reverse complement of the same window scored with the
  same PWM. Coordinates are always reported on the forward strand, so a
  ``-`` hit at ``[start, end)`` means the reverse complement of
  ``seq[start:end]`` matched.

Overlapping hits are all kept, each with its own identity
(sequence id, strand, start, end).

Unknown bases ('N' after parsing) follow the declared policy:

* ``skip``        — the window is not scored; it is counted as skipped;
* ``marginalize`` — an unknown position contributes its expected log-odds
  under the background, sum_b bg_b * w_{pos,b}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.errors import DomainError, ErrorCategory
from app.pwm import PWM
from app.sequence import SequenceRecord
from app.significance import (
    ScoreDistribution,
    benjamini_hochberg,
    bonferroni,
    pvalue_at_least,
)

_BASE_TO_IDX = {"A": 0, "C": 1, "G": 2, "T": 3}
_COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A"}

#: Tolerance when comparing a window score against the threshold.
_THRESHOLD_TOLERANCE = 1e-9


def reverse_complement(seq: str) -> str:
    """Reverse complement; unknown bases map to 'N'."""
    return "".join(_COMPLEMENT.get(ch, "N") for ch in reversed(seq))


def marginalized_position_scores(pwm: PWM) -> np.ndarray:
    """Expected per-position log-odds under the background (for 'marginalize')."""
    return pwm.log_odds @ pwm.background_vector


@dataclass(frozen=True)
class Hit:
    seq_id: str
    strand: str  # '+' or '-'
    start: int  # 0-based, forward-strand coordinates, half-open [start, end)
    end: int
    matched: str  # forward-strand sequence of the window
    score: float
    pvalue: float
    bonferroni: float
    benjamini_hochberg: float


@dataclass
class ScanOutcome:
    hits: list[Hit] = field(default_factory=list)
    evaluated_windows: int = 0
    skipped_windows: int = 0
    warnings: list[dict] = field(default_factory=list)


def _score_encoded(pwm: PWM, encoded: list[int], marginalized: np.ndarray) -> float:
    total = 0.0
    for pos, base_idx in enumerate(encoded):
        if base_idx >= 0:
            total += float(pwm.log_odds[pos, base_idx])
        else:
            total += float(marginalized[pos])
    return total


def scan_records(
    pwm: PWM,
    dist: ScoreDistribution,
    records: list[SequenceRecord],
    threshold: float,
    unknown_policy: str,
) -> ScanOutcome:
    """Scan all records on both strands and calibrate hits against ``dist``.

    P-values are computed for every evaluated window; multiple-testing
    correction (Bonferroni and Benjamini-Hochberg) is applied over the full
    set of m evaluated windows, then windows scoring at or above
    ``threshold`` are reported as hits with their adjusted values.
    """
    if unknown_policy not in ("skip", "marginalize"):
        raise DomainError(
            ErrorCategory.UNKNOWN_POLICY_INVALID,
            f"unknown-base policy must be one of 'skip' or 'marginalize', got {unknown_policy!r}",
            {"policy": unknown_policy},
        )

    outcome = ScanOutcome()
    marginalized = marginalized_position_scores(pwm)
    k = pwm.length

    # (seq_id, strand, start, matched, score) for every evaluated window.
    evaluated: list[tuple[str, str, int, str, float]] = []

    for record in records:
        seq = record.bases
        length = len(seq)
        if length < k:
            outcome.warnings.append(
                {
                    "category": "SEQUENCE_SHORTER_THAN_MOTIF",
                    "seq_id": record.seq_id,
                    "message": (
                        f"sequence '{record.seq_id}' (length {length}) is shorter than "
                        f"the motif (length {k}); no windows were evaluated for it"
                    ),
                }
            )
            continue
        for start in range(0, length - k + 1):
            window = seq[start : start + k]
            for strand, oriented in (("+", window), ("-", reverse_complement(window))):
                encoded = [_BASE_TO_IDX.get(ch, -1) for ch in oriented]
                if -1 in encoded and unknown_policy == "skip":
                    outcome.skipped_windows += 1
                    continue
                score = _score_encoded(pwm, encoded, marginalized)
                evaluated.append((record.seq_id, strand, start, window, score))

    outcome.evaluated_windows = len(evaluated)
    if not evaluated:
        return outcome

    pvalues = [pvalue_at_least(dist, score) for *_rest, score in evaluated]
    m = len(evaluated)
    bonf = bonferroni(pvalues, m)
    bh = benjamini_hochberg(pvalues, m)

    hits: list[Hit] = []
    for (seq_id, strand, start, window, score), p, pb, ph in zip(evaluated, pvalues, bonf, bh):
        if score >= threshold - _THRESHOLD_TOLERANCE:
            hits.append(
                Hit(
                    seq_id=seq_id,
                    strand=strand,
                    start=start,
                    end=start + k,
                    matched=window,
                    score=score,
                    pvalue=p,
                    bonferroni=pb,
                    benjamini_hochberg=ph,
                )
            )
    hits.sort(key=lambda h: (h.seq_id, h.start, h.strand))
    outcome.hits = hits
    return outcome
