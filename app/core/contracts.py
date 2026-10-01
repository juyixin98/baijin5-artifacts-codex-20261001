"""Typed result / error objects for the statistical core."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal


class FitError(ValueError):
    """Raised when a requested fit is structurally impossible.

    Callers (API / experiments) must convert this into an explicit FAILED
    status carrying the reason — never into a successful-but-empty result.
    """


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"   # estimate produced, but a diagnostic is concerning


class FailureCategory(str, Enum):
    INSUFFICIENT_DATA = "insufficient_data"
    RANK_DEFICIENT = "rank_deficient"
    SINGULAR_DESIGN = "singular_design"
    EMPTY_BANDWIDTH = "empty_bandwidth"
    BAD_INPUT = "bad_input"
    BANDWIDTH_INFEASIBLE = "bandwidth_infeasible"
    NON_IDENTIFIABLE = "non_identifiable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SideFit:
    """Local-linear fit on one side of the cutoff."""

    side: Literal["left", "right"]
    intercept: float
    slope: float
    bandwidth: float
    n: int
    sum_weights: float
    se_homoskedastic: float
    se_hc1: float
    se_hc2: float
    sse: float
    support_min: float          # min |x-c| with positive weight
    support_max: float          # max |x-c| with positive weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "intercept": self.intercept,
            "slope": self.slope,
            "bandwidth": self.bandwidth,
            "n": self.n,
            "sum_weights": self.sum_weights,
            "se_homoskedastic": self.se_homoskedastic,
            "se_hc1": self.se_hc1,
            "se_hc2": self.se_hc2,
            "sse": self.sse,
            "support": {"min_distance": self.support_min,
                        "max_distance": self.support_max},
        }


@dataclass(frozen=True)
class RDResult:
    """Full output of one RD estimation run."""

    run_id: str
    status: RunStatus
    cutoff: float
    kernel: str
    bandwidth_method: str
    bandwidth_left: float
    bandwidth_right: float
    se_type: str
    tau: float | None
    se: float | None
    ci: tuple[float, float] | None
    z: float | None
    pvalue: float | None
    left: SideFit | None
    right: SideFit | None
    failure_category: FailureCategory | None
    failure_reason: str | None
    warnings: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    versions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "cutoff": self.cutoff,
            "kernel": self.kernel,
            "bandwidth_method": self.bandwidth_method,
            "bandwidths": {
                "left": self.bandwidth_left,
                "right": self.bandwidth_right,
            },
            "se_type": self.se_type,
            "estimate": {
                "tau": self.tau,
                "se": self.se,
                "ci95": list(self.ci) if self.ci is not None else None,
                "z": self.z,
                "pvalue": self.pvalue,
            },
            "left": self.left.to_dict() if self.left else None,
            "right": self.right.to_dict() if self.right else None,
            "failure_category": (
                self.failure_category.value if self.failure_category else None
            ),
            "failure_reason": self.failure_reason,
            "warnings": list(self.warnings),
            "diagnostics": self.diagnostics,
            "versions": self.versions,
        }
