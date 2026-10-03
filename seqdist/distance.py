"""Domain algorithms: site classification, p-distance, corrected distances.

Correction formulas (hand-checkable):
    JC69: d = -3/4 * ln(1 - 4p/3)              valid for p < 3/4
    K80:  d = -1/2 * ln(1 - 2P - Q) - 1/4 * ln(1 - 2Q)
          valid for 1 - 2P - Q > 0 and 1 - 2Q > 0

Domain contract: when a logarithm argument falls outside the valid domain the
estimate is reported with status SATURATED and value None. The absolute value
of a negative logarithm argument is NEVER taken.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .errors import NoValidSitesError
from .parsing import GAP_CHARS, UNAMBIGUOUS_BASES, ParsedSequences

# Per-valid-site outcome codes, also used by the bootstrap resampler.
SITE_MATCH = 0
SITE_TRANSITION = 1
SITE_TRANSVERSION = 2

_TRANSITION_PAIRS = (frozenset("AG"), frozenset("CT"))


class EstimateStatus(str, Enum):
    OK = "ok"
    SATURATED = "saturated"
    NON_ESTIMABLE = "non_estimable"


@dataclass(frozen=True)
class SiteStats:
    """Outcome of pairwise site classification."""

    n_valid: int
    n_match: int
    n_transition: int
    n_transversion: int
    n_excluded_gap: int
    n_excluded_ambiguous: int
    site_codes: tuple[int, ...]  # one SITE_* code per valid site, alignment order

    @property
    def n_mismatch(self) -> int:
        return self.n_transition + self.n_transversion

    @property
    def p(self) -> float:
        return self.n_mismatch / self.n_valid

    @property
    def transition_fraction(self) -> float:
        return self.n_transition / self.n_valid

    @property
    def transversion_fraction(self) -> float:
        return self.n_transversion / self.n_valid


@dataclass(frozen=True)
class DistanceEstimate:
    """One corrected (or uncorrected) distance value."""

    model: str
    value: float | None
    status: EstimateStatus
    reason: str | None = None


def classify_sites(pair: ParsedSequences) -> SiteStats:
    """Classify each alignment column.

    A site is valid only when BOTH sequences carry an unambiguous A/C/G/T.
    Sites with a gap or an IUPAC ambiguity code in either sequence are
    excluded (pairwise deletion) and counted by reason.
    """
    codes: list[int] = []
    n_match = n_ti = n_tv = n_gap = n_amb = 0
    for a, b in zip(pair.seq1, pair.seq2):
        if a in GAP_CHARS or b in GAP_CHARS:
            n_gap += 1
            continue
        if a not in UNAMBIGUOUS_BASES or b not in UNAMBIGUOUS_BASES:
            n_amb += 1
            continue
        if a == b:
            n_match += 1
            codes.append(SITE_MATCH)
        elif frozenset((a, b)) in _TRANSITION_PAIRS:
            n_ti += 1
            codes.append(SITE_TRANSITION)
        else:
            n_tv += 1
            codes.append(SITE_TRANSVERSION)
    stats = SiteStats(
        n_valid=n_match + n_ti + n_tv,
        n_match=n_match,
        n_transition=n_ti,
        n_transversion=n_tv,
        n_excluded_gap=n_gap,
        n_excluded_ambiguous=n_amb,
        site_codes=tuple(codes),
    )
    if stats.n_valid == 0:
        raise NoValidSitesError(
            "no valid comparison sites: every column contains a gap or an "
            "ambiguous base in at least one sequence",
            detail={
                "alignment_length": pair.alignment_length,
                "n_excluded_gap": n_gap,
                "n_excluded_ambiguous": n_amb,
            },
        )
    return stats


def correct(p: float, P: float, Q: float, model: str) -> DistanceEstimate:
    """Apply one model's correction to observed fractions.

    p: overall mismatch fraction; P: transition fraction; Q: transversion
    fraction. Fractions outside a model's valid domain yield SATURATED.
    """
    if model == "p":
        return DistanceEstimate(model="p", value=p, status=EstimateStatus.OK)
    if model == "jc69":
        arg = 1.0 - 4.0 * p / 3.0
        if arg <= 0.0:
            return DistanceEstimate(
                model="jc69",
                value=None,
                status=EstimateStatus.SATURATED,
                reason=f"p={p:.6f} gives log argument 1-4p/3={arg:.6f} <= 0",
            )
        return DistanceEstimate(
            model="jc69", value=-0.75 * math.log(arg), status=EstimateStatus.OK
        )
    if model == "k80":
        arg1 = 1.0 - 2.0 * P - Q
        arg2 = 1.0 - 2.0 * Q
        if arg1 <= 0.0 or arg2 <= 0.0:
            return DistanceEstimate(
                model="k80",
                value=None,
                status=EstimateStatus.SATURATED,
                reason=(
                    f"P={P:.6f}, Q={Q:.6f} give log arguments "
                    f"1-2P-Q={arg1:.6f}, 1-2Q={arg2:.6f}; both must be > 0"
                ),
            )
        value = -0.5 * math.log(arg1) - 0.25 * math.log(arg2)
        return DistanceEstimate(model="k80", value=value, status=EstimateStatus.OK)
    from .errors import InputValidationError

    raise InputValidationError("unknown substitution model", detail={"model": model})


def estimate_from_stats(stats: SiteStats, model: str) -> DistanceEstimate:
    """Corrected distance for classified sites under one model."""
    return correct(stats.p, stats.transition_fraction, stats.transversion_fraction, model)


def estimate_from_codes(site_codes: np.ndarray, model: str) -> DistanceEstimate:
    """Corrected distance from per-site outcome codes (bootstrap path)."""
    n = site_codes.size
    if n == 0:
        raise NoValidSitesError("no valid comparison sites in resample")
    n_ti = int(np.count_nonzero(site_codes == SITE_TRANSITION))
    n_tv = int(np.count_nonzero(site_codes == SITE_TRANSVERSION))
    p = (n_ti + n_tv) / n
    return correct(p, n_ti / n, n_tv / n, model)
