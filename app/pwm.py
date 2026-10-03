"""Position weight matrix (PWM) construction.

A motif is supplied as a count matrix (rows = positions, columns = A, C, G, T).
Counts are converted to probabilities with a background-weighted pseudocount

    p'_{i,b} = (n_{i,b} + pc * bg_b) / (N_i + pc)

and the scoring matrix is the log2 odds against the declared background:

    w_{i,b} = log2(p'_{i,b} / bg_b)

The background is validated strictly: every base must have a *positive*
probability (a zero would make the log-odds undefined) and the
probabilities must sum to 1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.config import ALPHABET
from app.errors import DomainError, ErrorCategory

_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class PWM:
    log_odds: np.ndarray  # shape (k, 4), column order = ALPHABET
    background: dict[str, float]
    pseudocount: float

    @property
    def length(self) -> int:
        return int(self.log_odds.shape[0])

    @property
    def background_vector(self) -> np.ndarray:
        return np.array([self.background[b] for b in ALPHABET], dtype=float)


def validate_background(background: dict[str, float]) -> None:
    if not isinstance(background, dict):
        raise DomainError(ErrorCategory.INVALID_BACKGROUND, "background must be a mapping of base to probability")
    keys = set(background)
    if keys != set(ALPHABET):
        raise DomainError(
            ErrorCategory.INVALID_BACKGROUND,
            f"background must define exactly the bases {sorted(ALPHABET)}",
            {"provided": sorted(keys)},
        )
    for base in ALPHABET:
        value = background[base]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DomainError(
                ErrorCategory.INVALID_BACKGROUND,
                f"background probability for {base} is not a number",
                {"base": base, "value": repr(value)},
            )
        if value <= 0.0:
            raise DomainError(
                ErrorCategory.ZERO_BACKGROUND_PROBABILITY,
                f"background probability for base {base} is {value}; "
                "log-odds scoring requires strictly positive probabilities",
                {"base": base, "value": value},
            )
    total = sum(background[b] for b in ALPHABET)
    if abs(total - 1.0) > _SUM_TOLERANCE:
        raise DomainError(
            ErrorCategory.BACKGROUND_NOT_NORMALIZED,
            f"background probabilities sum to {total}, expected 1.0",
            {"sum": total, "tolerance": _SUM_TOLERANCE},
        )


def build_pwm(
    counts: list[list[float]],
    background: dict[str, float],
    pseudocount: float,
    max_motif_length: int,
) -> PWM:
    """Build a log2-odds PWM from a count matrix. Raises DomainError on bad input."""
    validate_background(background)
    if not isinstance(pseudocount, (int, float)) or isinstance(pseudocount, bool) or pseudocount <= 0:
        raise DomainError(
            ErrorCategory.INVALID_PSEUDOCOUNT,
            f"pseudocount must be a positive number, got {pseudocount!r}",
            {"pseudocount": repr(pseudocount)},
        )

    try:
        arr = np.asarray(counts, dtype=float)
    except (ValueError, TypeError) as exc:
        raise DomainError(
            ErrorCategory.INVALID_MOTIF,
            "motif count matrix must be a rectangular numeric array",
            {"reason": str(exc)},
        ) from exc
    if arr.ndim != 2 or arr.shape[1] != len(ALPHABET):
        raise DomainError(
            ErrorCategory.INVALID_MOTIF,
            "motif count matrix must be a 2-D array with exactly 4 columns (A, C, G, T)",
            {"shape": list(arr.shape)},
        )
    k = int(arr.shape[0])
    if k < 1:
        raise DomainError(ErrorCategory.INVALID_MOTIF, "motif must have at least one position")
    if k > max_motif_length:
        raise DomainError(
            ErrorCategory.MOTIF_TOO_LONG,
            f"motif length {k} exceeds the exact-enumeration limit of {max_motif_length}",
            {"length": k, "max_motif_length": max_motif_length},
        )
    if not np.isfinite(arr).all() or (arr < 0).any():
        raise DomainError(ErrorCategory.INVALID_MOTIF, "motif counts must be finite and non-negative")
    row_sums = arr.sum(axis=1)
    if (row_sums <= 0).any():
        bad = [int(i) for i in np.nonzero(row_sums <= 0)[0]]
        raise DomainError(
            ErrorCategory.INVALID_MOTIF,
            "every motif position must have a positive total count",
            {"zero_count_positions": bad},
        )

    bg_vec = np.array([background[b] for b in ALPHABET], dtype=float)
    probs = (arr + pseudocount * bg_vec) / (row_sums[:, None] + pseudocount)
    log_odds = np.log2(probs / bg_vec)
    return PWM(log_odds=log_odds, background=dict(background), pseudocount=float(pseudocount))
