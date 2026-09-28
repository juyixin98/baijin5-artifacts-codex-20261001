"""Statistical contract: configuration, validated data records, result objects.

This module defines *what* an AIPW run means (the estimand, the nuisance models,
the fold structure) and *what* it returns. It contains no numerical estimation
logic on purpose so that the contract can be imported and checked independently
of the kernel.

Boundary semantics
-------------------
* Inputs are validated at the system boundary (FastAPI request -> ``Dataset``).
* Propensity scores are bounded in ``(0, 1)``; trimming is explicit, never silent.
* The AIPW point estimate is always produced; it is unbiased/consistent under the
  double-robust condition stated in ``double_robust_condition`` — NOT under
  arbitrary misspecification.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np


# --------------------------------------------------------------------------- #
# Error taxonomy (input / state / resource / computation are distinguishable)
# --------------------------------------------------------------------------- #
class ErrorCategory(str, Enum):
    INPUT = "input_error"            # malformed or semantically invalid user input
    STATE = "state_conflict"         # run id reused / illegal state transition
    RESOURCE = "resource_exhausted"  # memory / time / storage budget
    COMPUTATION = "computation_failed"  # numerical failure inside the kernel


class AipwError(Exception):
    """Base class carrying a machine-readable category and replay context."""

    category: ErrorCategory = ErrorCategory.COMPUTATION

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
        }


class InputError(AipwError):
    category = ErrorCategory.INPUT


class StateConflictError(AipwError):
    category = ErrorCategory.STATE


class ResourceExhaustedError(AipwError):
    category = ErrorCategory.RESOURCE


class ComputationFailure(AipwError):
    category = ErrorCategory.COMPUTATION


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class Estimand(str, Enum):
    ATE = "ATE"   # average treatment effect E[Y(1) - Y(0)]
    ATT = "ATT"   # average effect on the treated (semantics documented in estimators)


@dataclass(frozen=True)
class TrimConfig:
    enabled: bool = True
    lower: float = 0.01
    upper: float = 0.99

    def __post_init__(self) -> None:
        if not (0.0 < self.lower < self.upper < 1.0):
            raise InputError(
                "trim bounds must satisfy 0 < lower < upper < 1",
                details={"lower": self.lower, "upper": self.upper},
            )


@dataclass(frozen=True)
class PropensityConfig:
    type: str = "logistic"
    l2_penalty: float = 0.0
    max_iter: int = 200
    tol: float = 1e-10

    def __post_init__(self) -> None:
        if self.type != "logistic":
            raise InputError(f"unsupported propensity model: {self.type!r}")
        if self.l2_penalty < 0:
            raise InputError("l2_penalty must be non-negative")
        if self.max_iter <= 0:
            raise InputError("max_iter must be positive")
        if self.tol <= 0:
            raise InputError("tol must be positive")


@dataclass(frozen=True)
class OutcomeConfig:
    type: str = "ols"
    intercept: bool = True
    standardize: bool = True

    def __post_init__(self) -> None:
        if self.type != "ols":
            raise InputError(f"unsupported outcome model: {self.type!r}")


@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool = False
    id_field: str = "cluster_id"


@dataclass(frozen=True)
class JobConfig:
    storage: str = "sqlite"
    database: str = "aipw_jobs.db"
    stale_after_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.storage != "sqlite":
            raise InputError(f"unsupported job storage: {self.storage!r}")
        if self.stale_after_seconds <= 0:
            raise InputError("stale_after_seconds must be positive")


@dataclass(frozen=True)
class Config:
    folds: int = 5
    trim_propensity: TrimConfig = field(default_factory=TrimConfig)
    stabilize_weight: bool = False
    propensity_model: PropensityConfig = field(default_factory=PropensityConfig)
    outcome_models: OutcomeConfig = field(default_factory=OutcomeConfig)
    estimand: Estimand = Estimand.ATE
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    jobs: JobConfig = field(default_factory=JobConfig)

    def __post_init__(self) -> None:
        if self.folds < 2:
            raise InputError("folds must be >= 2 for cross-fitting")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        raw = dict(raw)  # do not mutate caller data
        raw["trim_propensity"] = TrimConfig(**raw.get("trim_propensity", {}))
        raw["propensity_model"] = PropensityConfig(**raw.get("propensity_model", {}))
        raw["outcome_models"] = OutcomeConfig(**raw.get("outcome_models", {}))
        raw["cluster"] = ClusterConfig(**raw.get("cluster", {}))
        raw["jobs"] = JobConfig(**raw.get("jobs", {}))
        if isinstance(raw.get("estimand"), str):
            raw["estimand"] = Estimand(raw["estimand"])
        return cls(**raw)

    @classmethod
    def default(cls) -> "Config":
        path = Path(__file__).resolve().parents[2] / "config" / "defaults.json"
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Validated data record
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Dataset:
    """Validated analysis dataset.

    ``x``     : (n, p) float covariate matrix
    ``a``     : (n,) binary treatment in {0, 1}
    ``y``     : (n,) float outcome
    ``clusters``: optional (n,) integer cluster ids; independent units are clusters
    """

    x: Any  # numpy ndarray (kept un-typed at import time to keep module light)
    a: Any
    y: Any
    clusters: Any | None = None

    @property
    def n(self) -> int:
        return int(self.x.shape[0])

    @property
    def p(self) -> int:
        return int(self.x.shape[1])


# --------------------------------------------------------------------------- #
# Result objects
# --------------------------------------------------------------------------- #
class RunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class FoldDiagnostics:
    fold: int
    train_size: int
    valid_size: int
    treated_train: int
    control_train: int
    prop_min: float
    prop_max: float
    outcome0_train_rss: float
    outcome1_train_rss: float
    # means of the STANDARDIZED design used inside the fit; proves no leakage:
    # for a train-only scaler these must be ~0 only on training rows, not on
    # validation rows (the diagnostic layer compares train vs valid explicitly).
    train_x_mean_abs_max: float


@dataclass(frozen=True)
class EstimateResult:
    run_id: str
    estimand: str
    point: float
    se: float
    variance: float
    ci_lower: float
    ci_upper: float
    independent_units: int
    clustered: bool
    method: str
    folds: int
    n: int
    # component estimators used for cross-checks and diagnostics
    gcomp_point: float
    ipw_point: float
    aipw_point: float
    trimmed_fraction: float
    stabilized: bool
    fold_diagnostics: tuple[FoldDiagnostics, ...]
    # full-sample (in-sample, biased) g-comp fit kept ONLY as a leakage comparator
    insample_gcomp_point: float
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fold_diagnostics"] = [dict(f) for f in d["fold_diagnostics"]]
        return _json_safe(d)
def _json_safe(obj: Any) -> Any:
    """Convert nested dataclass-derived values into strict-JSON form.

    Non-finite floats (NaN from ATT's absent gcomp/ipw comparators) become
    JSON ``null`` rather than the non-standard ``NaN`` token; numpy scalars
    become plain Python numbers.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return _json_safe(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


DOUBLE_ROBUST_CONDITION = (
    "AIPW is doubly robust: the ATE estimate is (n^-1/2) consistent and "
    "asymptotically normal with the influence-function variance if AT LEAST ONE "
    "of (a) the propensity model P(A=1|X) or (b) BOTH outcome regressions "
    "E[Y|X,A=0], E[Y|X,A=1] is correctly specified (plus positivity and i.i.d. "
    "sampling). If both are misspecified, the estimate is generally biased and "
    "the nominal CI need not cover; closeness of the IPW, g-computation and AIPW "
    "points is a diagnostic hint, not a correctness guarantee."
)
