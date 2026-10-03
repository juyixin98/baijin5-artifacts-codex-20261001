"""Graph-cut binary segmentation service."""

from .config import AppConfig
from .energy import evaluate_energy, validate_spec
from .errors import (
    ComputationError,
    ErrorCategory,
    GraphCutError,
    InputValidationError,
    NotFoundError,
    ResourceExhaustedError,
    StateConflictError,
)
from .models import (
    CutCertificate,
    EnergyDecomposition,
    EnergySpec,
    ImageContract,
    PairwiseTerm,
    SeedSet,
    SolveResult,
)
from .pipeline import run_pipeline
from .specs import build_spec

__all__ = [
    "AppConfig",
    "ComputationError",
    "CutCertificate",
    "EnergyDecomposition",
    "EnergySpec",
    "ErrorCategory",
    "GraphCutError",
    "ImageContract",
    "InputValidationError",
    "NotFoundError",
    "PairwiseTerm",
    "ResourceExhaustedError",
    "SeedSet",
    "SolveResult",
    "StateConflictError",
    "build_spec",
    "evaluate_energy",
    "run_pipeline",
    "validate_spec",
]
