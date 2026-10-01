"""Statistical contract: enums, typed results, failure categories and config.

Everything crossing a layer boundary (kernel <-> service <-> API <-> DB) is
described by the types in this module so that the statistical contract is
explicit rather than encoded in loose dicts.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Failure categories
# --------------------------------------------------------------------------- #
class ErrorCode(str, enum.Enum):
    """Every error the backend can return has an explicit category.

    Nothing is ever collapsed into a generic success: validation failures,
    degenerate data and internal errors are distinguished by code.
    """

    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    EMPTY_DATA = "EMPTY_DATA"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NON_BINARY_TREATMENT = "NON_BINARY_TREATMENT"
    MISSING_ARM = "MISSING_ARM"
    ZERO_VARIANCE_OUTCOME = "ZERO_VARIANCE_OUTCOME"
    ZERO_VARIANCE_COVARIATE = "ZERO_VARIANCE_COVARIATE"
    MISSING_VALUES_PRESENT = "MISSING_VALUES_PRESENT"
    COLLINEAR_COVARIATES = "COLLINEAR_COVARIATES"
    LEAKAGE_DETECTED = "LEAKAGE_DETECTED"
    UNKNOWN_COVARIATE = "UNKNOWN_COVARIATE"
    DUPLICATE_COLUMN = "DUPLICATE_COLUMN"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CONFIG_ERROR = "CONFIG_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class EstimationError(Exception):
    """Domain error carrying a machine-readable :class:`ErrorCode`."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}


# --------------------------------------------------------------------------- #
# Statistical enums
# --------------------------------------------------------------------------- #
class Estimator(str, enum.Enum):
    GROUPS = "groups_unadjusted"
    CUPED = "cuped"
    LIN = "lin_ancova"


class ThetaSource(str, enum.Enum):
    """Where the CUPED adjustment coefficient theta is estimated from.

    ``control_pre`` (default, Deng et al. 2013 / Microsoft CUPED): theta is
    the regression slope of outcome on the PRE-treatment covariate fitted on
    the **control arm only**. This guarantees that randomisation is not
    disturbed and the treatment-effect estimator stays unbiased.
    """

    CONTROL_PRE = "control_pre"
    POOLED_PRE = "pooled_pre"
    GIVEN = "given"


class SEType(str, enum.Enum):
    """Standard error family, matched to the sample/grouping design."""

    WELCH = "welch"          # independent groups, unequal variances
    POOLED = "pooled"        # independent groups, common variance
    HC1 = "hc1"              # heteroskedasticity-robust OLS sandwich
    HC0 = "hc0"
    CLASSICAL = "classical"  # sigma^2 (X'X)^-1, residual homoskedasticity


class MissingPolicy(str, enum.Enum):
    FAIL = "fail"            # reject data containing NaN/None (default)
    COMPLETE_CASES = "complete_cases"  # row-wise deletion, reported explicitly
    MEAN_IMPUTE = "mean_impute"        # per-column mean (also reported)


class ZeroVariancePolicy(str, enum.Enum):
    DROP = "drop"      # silently *flag and drop* constant covariates
    FAIL = "fail"      # reject the run
    KEEP = "keep"      # pass through (matrix algebra will fail if collinear)


class LeakagePolicy(str, enum.Enum):
    FLAG = "flag"      # warn, still estimate (default)
    FAIL = "fail"      # reject suspected post-treatment covariates
    IGNORE = "ignore"  # no diagnostics


# --------------------------------------------------------------------------- #
# Typed result structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GroupSummary:
    arm: int
    n: int
    mean: float
    variance: float
    se_mean: float


@dataclass(frozen=True)
class Estimate:
    estimator: str
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    t_stat: float
    p_value: float
    df: float
    n_treatment: int
    n_control: int
    se_type: str
    residual_variance: float | None = None
    r_squared: float | None = None
    # CUPED-specific evidence
    theta: tuple[float, ...] | None = None
    theta_source: str | None = None
    theta_se: tuple[float, ...] | None = None
    covariate_names: tuple[str, ...] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CovariateDiagnostic:
    name: str
    included: bool
    reason_excluded: str | None
    overall_mean: float | None
    overall_std: float | None
    mean_treatment: float | None
    mean_control: float | None
    smd: float                 # standardised mean difference (0 when excluded)
    smd_threshold: float
    balance_ok: bool
    corr_with_outcome: float   # pooled Pearson correlation (descriptive)
    corr_with_treatment: float # point-biserial corr with assignment
    missing_count: int
    zero_variance: bool
    leakage_flag: bool
    leakage_reason: str | None
    # True only on the authoritative provenance declaration (post-treatment);
    # a purely statistical imbalance hint leaves this False.
    provenance_post_treatment: bool = False


@dataclass(frozen=True)
class CrossCheck:
    name: str
    passed: bool
    detail: str
    values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    data_fingerprint: str
    created_at: str
    experiment_name: str
    outcome_column: str
    treatment_column: str
    requested_covariates: tuple[str, ...]
    used_covariates: tuple[str, ...]
    dropped_covariates: tuple[str, ...]
    n_rows: int
    n_complete_rows: int
    n_dropped_rows: int
    missing_policy: str
    theta_source: str
    se_type: str
    unadjusted: Estimate
    cuped: Estimate
    lin: Estimate
    groups: tuple[GroupSummary, ...]
    diagnostics: tuple[CovariateDiagnostic, ...]
    cross_checks: tuple[CrossCheck, ...]
    warnings: tuple[str, ...]
    versions: dict[str, str]
    config_snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Settings:
    theta_source: ThetaSource = ThetaSource.CONTROL_PRE
    se_type: SEType = SEType.WELCH
    regression_interactions: bool = True   # Lin (2013) estimator
    missing_policy: MissingPolicy = MissingPolicy.FAIL
    zero_variance_policy: ZeroVariancePolicy = ZeroVariancePolicy.DROP
    leakage_policy: LeakagePolicy = LeakagePolicy.FLAG
    smd_threshold: float = 0.20
    alpha: float = 0.01                    # leakage correlation test level
    ci_level: float = 0.95

    @staticmethod
    def from_file(path: str | Path) -> "Settings":
        raw = json.loads(Path(path).read_text())
        return Settings.from_dict(raw.get("defaults", {}))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Settings":
        field_names = Settings.__dataclass_fields__.keys()
        enums = {
            "theta_source": ThetaSource,
            "se_type": SEType,
            "missing_policy": MissingPolicy,
            "zero_variance_policy": ZeroVariancePolicy,
            "leakage_policy": LeakagePolicy,
        }
        kwargs: dict[str, Any] = {}
        for key, value in d.items():
            if key not in field_names:
                continue
            if key in enums:
                try:
                    kwargs[key] = enums[key](value)
                except ValueError as exc:
                    raise EstimationError(
                        ErrorCode.CONFIG_ERROR,
                        f"invalid value {value!r} for {key}",
                    ) from exc
            else:
                kwargs[key] = value
        return Settings(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("theta_source", "se_type", "missing_policy",
                    "zero_variance_policy", "leakage_policy"):
            out[key] = getattr(self, key).value
        return out
