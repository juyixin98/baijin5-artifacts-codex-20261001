"""Domain algorithms: site classification and distance models.

Site classification (per aligned column, pairwise deletion)
-----------------------------------------------------------
A site is *comparable* only when BOTH sequences carry an unambiguous
A/C/G/T base. Sites where either sequence has a gap ('-'), 'N', or an
IUPAC ambiguity code are excluded from every numerator and denominator.

For comparable sites:
- match        : identical bases
- transition   : A<->G or C<->T
- transversion : any other difference

Distance models (distinct assumptions, see README)
--------------------------------------------------
- p-distance : p = (ti + tv) / n. No substitution model; raw proportion.
- JC69       : assumes equal base frequencies and one rate for all
               substitutions. d = -3/4 * ln(1 - 4p/3).
               Valid domain: 1 - 4p/3 > 0  <=>  p < 3/4.
- K2P        : Kimura 2-parameter; separate transition/transversion rates.
               d = -1/2 * ln(1 - 2P - Q) - 1/4 * ln(1 - 2Q),
               P = ti/n, Q = tv/n.
               Valid domain: 1 - 2P - Q > 0 AND 1 - 2Q > 0.

Out-of-domain handling: the log argument is checked BEFORE calling log.
Outside the valid domain the estimate is reported with status
"saturated" and distance=None. We never take abs() of a negative log
argument and never return a fabricated positive number.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

# Site outcome categories, also used as integer codes by the bootstrapper.
MATCH = 0
TRANSITION = 1
TRANSVERSION = 2

_TRANSITION_PAIRS = frozenset({("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")})


class Model(str, enum.Enum):
    P_DISTANCE = "p"
    JC69 = "jc69"
    K2P = "k2p"


class EstimateStatus(str, enum.Enum):
    OK = "ok"
    SATURATED = "saturated"  # outside the model's valid domain
    UNDEFINED = "undefined"  # no comparable sites


@dataclass(frozen=True)
class SiteCounts:
    """Aggregated comparable-site counts for one sequence pair."""

    n_valid: int
    n_match: int
    n_transition: int
    n_transversion: int

    @property
    def n_mismatch(self) -> int:
        return self.n_transition + self.n_transversion

    @property
    def p(self) -> float | None:
        return None if self.n_valid == 0 else self.n_mismatch / self.n_valid

    @property
    def P(self) -> float | None:  # transition proportion
        return None if self.n_valid == 0 else self.n_transition / self.n_valid

    @property
    def Q(self) -> float | None:  # transversion proportion
        return None if self.n_valid == 0 else self.n_transversion / self.n_valid


def classify_site(base_a: str, base_b: str) -> int | None:
    """Classify one aligned column; None = not comparable (missing/ambiguous)."""
    if base_a not in ("A", "C", "G", "T") or base_b not in ("A", "C", "G", "T"):
        return None
    if base_a == base_b:
        return MATCH
    if (base_a, base_b) in _TRANSITION_PAIRS:
        return TRANSITION
    return TRANSVERSION


def site_outcomes(seq_a: str, seq_b: str) -> list[int]:
    """Per-site outcome codes (MATCH/TRANSITION/TRANSVERSION) for comparable sites."""
    outcomes: list[int] = []
    for a, b in zip(seq_a, seq_b):
        code = classify_site(a, b)
        if code is not None:
            outcomes.append(code)
    return outcomes


def count_sites(seq_a: str, seq_b: str) -> SiteCounts:
    n_match = n_ti = n_tv = 0
    for code in site_outcomes(seq_a, seq_b):
        if code == MATCH:
            n_match += 1
        elif code == TRANSITION:
            n_ti += 1
        else:
            n_tv += 1
    return SiteCounts(
        n_valid=n_match + n_ti + n_tv,
        n_match=n_match,
        n_transition=n_ti,
        n_transversion=n_tv,
    )


def counts_from_outcomes(outcomes: list[int]) -> SiteCounts:
    n_match = outcomes.count(MATCH)
    n_ti = outcomes.count(TRANSITION)
    n_tv = outcomes.count(TRANSVERSION)
    return SiteCounts(
        n_valid=len(outcomes), n_match=n_match, n_transition=n_ti, n_transversion=n_tv
    )


@dataclass(frozen=True)
class DistanceEstimate:
    model: Model
    status: EstimateStatus
    distance: float | None
    p_distance: float | None
    rationale: tuple[str, ...] = field(default_factory=tuple)


def estimate_distance(counts: SiteCounts, model: Model) -> DistanceEstimate:
    """Apply one distance model to aggregated counts.

    Returns a status-carrying estimate; never raises for domain issues.
    """
    if counts.n_valid == 0:
        return DistanceEstimate(
            model=model,
            status=EstimateStatus.UNDEFINED,
            distance=None,
            p_distance=None,
            rationale=("no comparable sites: every column has a gap or ambiguous base "
                       "in at least one sequence",),
        )

    p = counts.p
    assert p is not None
    if model is Model.P_DISTANCE:
        return DistanceEstimate(
            model=model,
            status=EstimateStatus.OK,
            distance=p,
            p_distance=p,
            rationale=(f"p = (ti+tv)/n = {counts.n_mismatch}/{counts.n_valid} = {p:.6f}",),
        )

    if model is Model.JC69:
        arg = 1.0 - 4.0 * p / 3.0
        rationale = [f"JC69 log argument 1 - 4p/3 = {arg:.6f} (p = {p:.6f})"]
        if arg <= 0.0:
            rationale.append(
                "argument <= 0: p >= 3/4 is outside the JC69 valid domain; "
                "distance is saturated / not estimable (no abs() applied)"
            )
            return DistanceEstimate(Model.JC69, EstimateStatus.SATURATED, None, p, tuple(rationale))
        d = -0.75 * math.log(arg)
        rationale.append(f"d = -3/4 * ln({arg:.6f}) = {d:.6f}")
        return DistanceEstimate(Model.JC69, EstimateStatus.OK, d, p, tuple(rationale))

    # K2P
    P = counts.P
    Q = counts.Q
    assert P is not None and Q is not None
    arg1 = 1.0 - 2.0 * P - Q
    arg2 = 1.0 - 2.0 * Q
    rationale = [
        f"K2P log arguments: 1 - 2P - Q = {arg1:.6f}, 1 - 2Q = {arg2:.6f} "
        f"(P = {P:.6f}, Q = {Q:.6f})"
    ]
    if arg1 <= 0.0 or arg2 <= 0.0:
        rationale.append(
            "at least one argument <= 0: outside the K2P valid domain; "
            "distance is saturated / not estimable (no abs() applied)"
        )
        return DistanceEstimate(Model.K2P, EstimateStatus.SATURATED, None, p, tuple(rationale))
    d = -0.5 * math.log(arg1) - 0.25 * math.log(arg2)
    rationale.append(f"d = -1/2*ln({arg1:.6f}) - 1/4*ln({arg2:.6f}) = {d:.6f}")
    return DistanceEstimate(Model.K2P, EstimateStatus.OK, d, p, tuple(rationale))
