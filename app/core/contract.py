"""Statistical contract for paired randomized experiments.

This module is the single source of truth for the conventions used everywhere
else in the service (kernel, evidence oracle, API, tests). Nothing here depends
on NumPy beyond the array conversion; the definitions themselves are written
out explicitly so they can be audited independently.

Design
------
Data are *paired* outcomes. For pair ``i`` we observe a treated outcome
``t_i`` and a control outcome ``c_i``; the observed within-pair difference is

    d_i = t_i - c_i.

Randomization set (paired design, contract point #1):
    Each pair carries its own fair, independent treatment assignment. A
    randomization draw is therefore a vector ``s in {-1, +1}^n``: flipping
    pair ``i`` swaps which of its two members is treated. There are exactly
    ``2^n`` assignments. Units are *never* shuffled across pair boundaries.

Sharp null of constant effect:
    H0(tau): Y(1) - Y(0) == tau for every unit (additive / constant effect).
    The adjusted residual is r_i(tau) = d_i - tau. Under H0(tau) the
    randomization distribution is obtained by independently sign-flipping the
    residuals: s_i * r_i(tau).

Two-sided statistic (identical definition for the test and the inversion,
contract point #2):

    T_tau(s) = | sum_i s_i * (d_i - tau) |.

The observed assignment is the all-+1 vector, so T_obs(tau) = |sum_i(d_i-tau)|.

Exact two-sided randomization p-value (observed assignment included):

    p(tau) = #{ s : T_tau(s) >= T_obs(tau) } / 2^n.

Monte-Carlo version (conditional on the observed draw, the standard +1
randomization p-value):

    p_MC(tau) = (1 + #{k : T_tau(s_k) >= T_obs(tau)}) / (1 + M),

where the M sign-flip vectors are drawn i.i.d. uniformly. Its Monte-Carlo
standard error is reported by callers.

Inversion: the (1-alpha) confidence set is

    C_alpha = { tau : p(tau) >= alpha }.

It is returned as an ordered set of disjoint intervals exactly as enumerated;
if the acceptance set has gaps they are preserved (it is never collapsed to a
single interval).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

STATISTIC_NAME = "abs_signed_sum"
TWO_SIDED_DEFINITION = "T(s;tau)=|sum_i s_i*(d_i-tau)|; p=#{T>=T_obs}/2^n"
OBSERVED_ASSIGNMENT = tuple()  # placeholder replaced conceptually by all +1 vector


class ContractError(ValueError):
    """Validation failure with a stable machine-readable ``code``.

    The API maps ``code`` to a categorized failure response, and the test
    suite asserts on the concrete categories.
    """

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class Pair:
    treated: float
    control: float


def _coerce_float(value: object, index: int, field: str) -> float:
    if isinstance(value, bool):
        raise ContractError(
            "NON_NUMERIC_OUTCOME",
            f"pairs[{index}].{field} must be numeric, got boolean",
            {"index": index, "field": field},
        )
    if not isinstance(value, (int, float, np.integer, np.floating)):
        raise ContractError(
            "NON_NUMERIC_OUTCOME",
            f"pairs[{index}].{field} must be numeric",
            {"index": index, "field": field},
        )
    out = float(value)
    if not math.isfinite(out):
        raise ContractError(
            "NON_FINITE_OUTCOME",
            f"pairs[{index}].{field} must be finite",
            {"index": index, "field": field},
        )
    return out


def validate_pairs(pairs: Sequence[Sequence[float]] | None,
                   differences: Sequence[float] | None = None) -> np.ndarray:
    """Validate API-level pair input and return the difference vector ``d``.

    Either ``pairs`` (iterable of ``[treated, control]``) or ``differences``
    (iterable of ``treated - control`` values) must be supplied.
    """
    if pairs is None and differences is None:
        raise ContractError(
            "MISSING_DATA", "provide either 'pairs' or 'differences'"
        )
    if pairs is not None and differences is not None:
        raise ContractError(
            "AMBIGUOUS_DATA", "provide only one of 'pairs' or 'differences'"
        )

    diffs: list[float] = []
    if differences is not None:
        if isinstance(differences, (str, bytes)):
            raise ContractError("MALFORMED_DATA", "'differences' must be a list")
        for i, value in enumerate(differences):
            diffs.append(_coerce_float(value, i, "difference"))
    else:
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Iterable):
            raise ContractError("MALFORMED_DATA", "'pairs' must be a list")
        for i, pair in enumerate(pairs):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ContractError(
                    "INVALID_PAIR_SHAPE",
                    f"pairs[{i}] must be a [treated, control] pair",
                    {"index": i},
                )
            treated = _coerce_float(pair[0], i, "treated")
            control = _coerce_float(pair[1], i, "control")
            diffs.append(treated - control)

    if len(diffs) < 2:
        raise ContractError(
            "TOO_FEW_PAIRS",
            "at least 2 pairs are required for a paired randomization analysis",
            {"n_pairs": len(diffs)},
        )
    return np.asarray(diffs, dtype=float)


def validate_alpha(alpha: float) -> float:
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool):
        raise ContractError("INVALID_ALPHA", "'alpha' must be a number")
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ContractError(
            "INVALID_ALPHA",
            "'alpha' must lie strictly between 0 and 1",
            {"alpha": alpha},
        )
    return alpha


def validate_effect(effect: object) -> float:
    effect = _coerce_float(effect, 0, "effect")
    return effect


def n_assignments(n_pairs: int) -> int:
    """Size of the paired randomization set: 2**n_pairs (never (2n)!)."""
    return 1 << int(n_pairs)


def statistic_abs_signed_sum(signed_residuals: np.ndarray) -> np.ndarray | float:
    """Two-sided statistic T = |sum of signed residuals|.

    Accepts a 1-D vector (one assignment) or a 2-D matrix (one assignment per
    row). The same function is used for the null test and for inversion.
    """
    arr = np.asarray(signed_residuals, dtype=float)
    return np.abs(arr.sum(axis=-1))
