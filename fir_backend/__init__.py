"""FIR estimation backend: regularized least squares over explicit contracts."""

from .contracts import EstimateParams, SampleBlock
from .errors import (
    ComputationError,
    ErrorCategory,
    FirBackendError,
    InputValidationError,
    ResourceExhaustedError,
    StateConflictError,
)
from .estimator import EstimateResult, FitDiagnostics, estimate_fir

__all__ = [
    "ComputationError",
    "ErrorCategory",
    "EstimateParams",
    "EstimateResult",
    "FirBackendError",
    "FitDiagnostics",
    "InputValidationError",
    "ResourceExhaustedError",
    "SampleBlock",
    "StateConflictError",
    "estimate_fir",
]

__version__ = "0.1.0"
