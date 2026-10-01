"""Statistical and API contract.

The contract makes every outcome *explicit*:

* ``RunStatus`` distinguishes a valid estimate from a non-identified design
  and from outright errors. An exception is never serialised as "success".
* Kernel / bandwidth / inference selections are closed enums so a run is
  reproducible from its request + recorded parameters.
* Inputs are validated at the boundary (pydantic) and again numerically
  inside the pipeline.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class KernelName(str, Enum):
    TRIANGULAR = "triangular"  # default; boundary optimal, compact support
    EPANECHNIKOV = "epanechnikov"
    UNIFORM = "uniform"


class BandwidthMethod(str, Enum):
    MANUAL = "manual"  # caller pins ``bandwidth``
    IK_ROT = "ik_rot"  # Imbens-Kalyanaraman style rule of thumb, per side


class InferenceMethod(str, Enum):
    HC3 = "hc3"  # heteroskedasticity-robust sandwich, small-sample corrected
    HC1 = "hc1"  # heteroskedasticity-robust sandwich, HC1 correction
    NONE = "none"  # point estimate only; CI omitted


class PolynomialDegree(int, Enum):
    LINEAR = 1  # the supported local model


class RunStatus(str, Enum):
    OK = "ok"
    UNIDENTIFIED = "unidentified"  # design/order condition fails; no CI claimed
    ERROR = "error"  # invalid input or numerical failure


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    INSUFFICIENT_DATA = "insufficient_data"
    SINGULAR_FIT = "singular_fit"
    BANDWIDTH_FAILED = "bandwidth_failed"
    INTERNAL_ERROR = "internal_error"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DiagnosticCode(str, Enum):
    DISCRETE_RUNNING_VAR = "discrete_running_var"
    MASS_AT_CUTOFF = "mass_at_cutoff"
    DENSITY_DISCONTINUITY = "density_discontinuity"
    SPARSE_SIDE = "sparse_side"
    EFFECTIVE_SAMPLE = "effective_sample"
    IDENTIFICATION_RANGE = "identification_range"
    POOR_CONDITION_NUMBER = "poor_condition_number"


class DataPoint(BaseModel):
    x: float
    y: float


class RDRequest(BaseModel):
    """Analysis request.

    ``x`` is the running/assignment variable, ``y`` the outcome, ``cutoff``
    the threshold. Treatment is ``x >= cutoff`` by default; set
    ``treatment_above=false`` for the reverse coding.
    """

    run_id: str | None = Field(
        default=None, description="Optional caller id; a UUID4 is generated otherwise."
    )
    input_label: str = Field(
        default="unlabeled",
        description="Free-text tag propagated into logs and the stored record.",
    )
    data: list[DataPoint] = Field(min_length=6)
    cutoff: float = 0.0
    kernel: KernelName = KernelName.TRIANGULAR
    bandwidth_method: BandwidthMethod = BandwidthMethod.IK_ROT
    bandwidth: float | None = Field(
        default=None,
        gt=0.0,
        description="Required for bandwidth_method=manual; ignored otherwise.",
    )
    bandwidth_multiplier: float = Field(
        default=1.0,
        gt=0.0,
        description="Scale applied to the selected bandwidth (sensitivity sweeps).",
    )
    inference: InferenceMethod = InferenceMethod.HC3
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    bootstrap_reps: int | None = Field(default=None, ge=0)
    bootstrap_seed: int | None = None
    treatment_above: bool = True
    polynomial_degree: PolynomialDegree = PolynomialDegree.LINEAR
    cluster_var: list[float | int | str] | None = Field(
        default=None,
        description="Optional cluster labels (one per observation); CRV bootstrap.",
    )

    @field_validator("data")
    @classmethod
    def _finite(cls, v: list[DataPoint]) -> list[DataPoint]:
        for p in v:
            if not (p.x == p.x and p.y == p.y):  # NaN check
                raise ValueError("x and y must be finite (NaN found)")
            if p.x in (float("inf"), float("-inf")) or p.y in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError("x and y must be finite (inf found)")
        return v

    @model_validator(mode="after")
    def _manual_requires_bandwidth(self) -> "RDRequest":
        if self.bandwidth_method is BandwidthMethod.MANUAL and self.bandwidth is None:
            raise ValueError(
                "bandwidth is required and must be > 0 when "
                "bandwidth_method='manual'"
            )
        return self


class SideFit(BaseModel):
    intercept: float
    slope: float
    bandwidth: float
    n_in_window: int
    effective_n: float  # sum of kernel weights
    condition_number: float
    x_at_cutoff_probe: float = Field(
        description="Furthest in-window distance on this side (ident. range bound)."
    )


class Diagnostic(BaseModel):
    code: DiagnosticCode
    severity: Severity
    message: str
    details: dict


class BandwidthReport(BaseModel):
    method: BandwidthMethod
    left: float
    right: float
    multiplier_applied: float
    notes: str = ""


class BootstrapReport(BaseModel):
    reps: int
    seed: int | None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    clustered: bool = False
    note: str = ""


class RDEstimate(BaseModel):
    tau: float = Field(description="Local-linear jump estimate at the cutoff.")
    tau_left: float
    tau_right: float
    se: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    z: float | None = None
    p_value: float | None = None
    bias_notes: str


class RDResponse(BaseModel):
    run_id: str
    input_label: str
    status: RunStatus
    error_code: ErrorCode | None = None
    error_message: str | None = None
    cutoff: float
    kernel: KernelName
    inference: InferenceMethod
    alpha: float
    bandwidth: BandwidthReport | None = None
    estimate: RDEstimate | None = None
    left_fit: SideFit | None = None
    right_fit: SideFit | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    bootstrap: BootstrapReport | None = None
    versions: dict[str, str] = Field(default_factory=dict)


class StoredRun(BaseModel):
    """Row projection of the SQLite ``runs`` table."""

    run_id: str
    input_label: str
    status: str
    created_at: str
    n_obs: int
    cutoff: float
    tau: float | None
    request_json: str
    response_json: str
