"""Statistical contract: immutable domain types shared across layers.

Design rules enforced by these types:

* Pre-treatment covariates are the *only* covariates allowed to enter an
  adjustment. A covariate declared ``pre_treatment=False`` is a post-treatment
  / leakage field and is rejected with ``LEAKED_COVARIATE``.
* Unadjusted and adjusted estimates are always reported side by side.
* All results are immutable frozen dataclasses; intermediate state is never
  mutated in place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissingStrategy(str, Enum):
    ERROR = "error"          # fail loudly on any missing covariate value
    MEAN_IMPUTE = "mean_impute"  # impute pooled observed mean, record count


class ZeroVarianceStrategy(str, Enum):
    DROP = "drop"    # drop the degenerate column, emit a warning
    ERROR = "error"  # reject the run


class ThetaSource(str, Enum):
    CONTROL = "control"       # classical CUPED: theta fit on control arm only
    POOLED_FWL = "pooled_fwl"  # pooled OLS coefficient after partialling out T


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CovariateDeclaration:
    name: str
    pre_treatment: bool


@dataclass(frozen=True)
class FailedRun:
    """A run that failed validation or estimation. Never reported as success."""

    run_id: str
    experiment_id: str
    status: str
    error_code: str
    message: str
    details: dict[str, Any]
    versions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectEstimate:
    """Treatment effect with a two-independent-group standard error.

    The standard error ``sqrt(var_1/n_1 + var_0/n_0)`` and the Welch degrees
    of freedom are the same grouping design for both the unadjusted and the
    adjusted outcome, so the two estimates are directly comparable.
    """

    kind: str
    estimate: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    df: float
    n_treatment: int
    n_control: int
    mean_treatment: float
    mean_control: float
    var_treatment: float
    var_control: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ThetaInfo:
    """Provenance of the adjustment coefficient."""

    source: str
    coefficients: dict[str, float]
    n_units_used: int
    n_arms_used: str  # "control" | "pooled"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CovariateDiagnostic:
    name: str
    pre_treatment: bool
    n_missing: int
    n_imputed: int
    variance: float
    zero_variance: bool
    dropped: bool
    mean_control: float
    mean_treatment: float
    standardized_mean_diff: float
    balance_z: float
    balance_p_value: float
    leakage_suspected: bool
    corr_outcome_control: float
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EstimationResult:
    run_id: str
    experiment_id: str
    status: str
    n: int
    n_treatment: int
    n_control: int
    covariates_requested: tuple[str, ...]
    covariates_used: tuple[str, ...]
    theta: ThetaInfo | None
    unadjusted: EffectEstimate
    adjusted: EffectEstimate | None
    se_reduction: float | None
    variance_reduction: float | None
    hc1_regression_se: float | None
    reference_regression: dict[str, Any] | None
    diagnostics: tuple[CovariateDiagnostic, ...]
    warnings: tuple[str, ...]
    versions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "status": self.status,
            "n": self.n,
            "n_treatment": self.n_treatment,
            "n_control": self.n_control,
            "covariates_requested": list(self.covariates_requested),
            "covariates_used": list(self.covariates_used),
            "theta": self.theta.to_dict() if self.theta else None,
            "unadjusted": self.unadjusted.to_dict(),
            "adjusted": self.adjusted.to_dict() if self.adjusted else None,
            "se_reduction": self.se_reduction,
            "variance_reduction": self.variance_reduction,
            "hc1_regression_se": self.hc1_regression_se,
            "reference_regression": self.reference_regression,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "warnings": list(self.warnings),
            "versions": self.versions,
        }
