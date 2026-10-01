"""Statistical contract types for paired randomization tests.

The types here pin down the three behavioural contracts of the service:

1. ``PairDesign`` is strictly paired: the randomization set contains exactly
   one independent treatment/control flip *per pair* (``2**n`` assignments).
   Individuals are never shuffled across pairs.
2. Every two-sided p-value is produced by one named definition
   (:class:`TwoSidedMethod`) and inversion reuses that *same* definition.
3. An inverted acceptance set is a union of components
   (:class:`SetComponent`).  When the acceptance set is disconnected the
   components are returned separately; they are never collapsed into a
   single interval.  A convex hull is offered only as explicitly labelled
   metadata.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class TwoSidedMethod(str, enum.Enum):
    """Supported definitions of a two-sided randomization p-value.

    ``ABS`` is the classical absolute-statistic ordering::

        p(tau) = P_null( |S_z(tau)| >= |S_obs(tau)| ).

    ``PROB`` is the probability (mass) ordering: a statistic value is as
    extreme as the observed value iff its null probability mass is no larger
    than the observed value's mass.  This is a legitimate two-sided
    definition whose acceptance region can be genuinely disconnected.
    """

    ABS = "two_sided_abs"
    PROB = "two_sided_prob"


class ComputationKind(str, enum.Enum):
    EXACT = "exact"  # full enumeration of the paired randomization set
    APPROXIMATE = "approximate"  # Monte Carlo / numerical approximation


class FailureCode(str, enum.Enum):
    """Explicit failure taxonomy (returned in-band, never raised as 500)."""

    INVALID_PAIRS = "invalid_pairs"  # unequal length, non-finite values
    TOO_FEW_PAIRS = "too_few_pairs"
    INVALID_ALPHA = "invalid_alpha"
    INVALID_TAU = "invalid_tau"
    INVALID_METHOD = "invalid_method"
    APPROXIMATION_UNRESOLVED = "approximation_unresolved"
    INTERNAL = "internal_error"


@dataclass(frozen=True)
class PairDesign:
    """Strictly paired design.

    ``treated`` and ``control`` are aligned by index: position ``i`` holds
    the two outcomes of pair ``i``.  Only within-pair treatment swaps are
    ever randomized.
    """

    treated: Tuple[float, ...]
    control: Tuple[float, ...]

    @property
    def n_pairs(self) -> int:
        return len(self.treated)

    @property
    def differences(self) -> Tuple[float, ...]:
        return tuple(t - c for t, c in zip(self.treated, self.control))

    @property
    def randomization_set_size(self) -> int:
        return 2**self.n_pairs


@dataclass(frozen=True)
class SetComponent:
    """One connected component of an acceptance set.

    Endpoints use ``None`` for unboundedness; ``*_closed`` states whether the
    endpoint itself belongs to the component.  A singleton (e.g. an isolated
    accepted breakpoint) has ``lower == upper`` with both ends closed.
    """

    lower: Optional[float]
    lower_closed: bool
    upper: Optional[float]
    upper_closed: bool

    @property
    def is_singleton(self) -> bool:
        return (
            self.lower is not None
            and self.upper is not None
            and self.lower == self.upper
            and self.lower_closed
            and self.upper_closed
        )

    def render(self) -> str:
        lb = "(" if self.lower is None or not self.lower_closed else "["
        rb = ")" if self.upper is None or not self.upper_closed else "]"
        lo = "-inf" if self.lower is None else f"{self.lower:.6g}"
        hi = "+inf" if self.upper is None else f"{self.upper:.6g}"
        return f"{lb}{lo}, {hi}{rb}"


@dataclass(frozen=True)
class PValueResult:
    tau: float
    method: TwoSidedMethod
    kind: ComputationKind
    p_value: float
    n_pairs: int
    randomization_set_size: int
    n_extreme: Optional[int] = None  # exact: count of assignments as/more extreme
    n_evaluated: Optional[int] = None  # approx: draws actually used
    standard_error: Optional[float] = None  # approx: MC standard error of p
    monte_carlo_error: Optional[float] = None  # approx: reported +/- error
    seed: Optional[int] = None
    rejected: Optional[bool] = None  # only set when an alpha was supplied
    alpha: Optional[float] = None
    uncertainty: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AcceptanceSetResult:
    """Inverted acceptance set under one *fixed* two-sided definition."""

    method: TwoSidedMethod
    alpha: float
    kind: ComputationKind
    components: Tuple[SetComponent, ...]
    n_pairs: int
    randomization_set_size: int
    # ``True`` only when the exact certified algorithm was used, so the
    # component union is exhaustive and boundaries are exact.
    certified: bool
    boundary_tolerance: Optional[float] = None  # approx: |boundary error|
    standard_error: Optional[float] = None  # approx: MC uncertainty at edges
    n_p_evaluations: Optional[int] = None
    hull: Optional[SetComponent] = None  # labelled convex hull only
    uncertainty: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_disconnected(self) -> bool:
        return len(self.components) > 1

    @property
    def is_empty(self) -> bool:
        return len(self.components) == 0
