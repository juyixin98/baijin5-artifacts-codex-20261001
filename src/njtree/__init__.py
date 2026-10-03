"""njtree -- deterministic neighbor-joining backend for synthetic distance matrices."""

from .errors import (
    ComputationError,
    ErrorCategory,
    InputValidationError,
    NJError,
    ResourceExhaustedError,
    StateConflictError,
    UnknownRunError,
)
from .matrix import collect_violations, validate_distance_matrix
from .models import (
    BuildParams,
    BuildResult,
    DistanceMatrix,
    NegativeBranchMode,
    ReplayReport,
)
from .nj import neighbor_joining
from .parsing import hamming_matrix, parse_fasta
from .provenance import ProvenanceStore
from .residuals import compute_residuals
from .service import TreeService
from .tree import leaf_map, patristic_distances, to_newick

__all__ = [
    "BuildParams",
    "BuildResult",
    "ComputationError",
    "DistanceMatrix",
    "ErrorCategory",
    "InputValidationError",
    "NJError",
    "NegativeBranchMode",
    "ProvenanceStore",
    "ReplayReport",
    "ResourceExhaustedError",
    "StateConflictError",
    "TreeService",
    "UnknownRunError",
    "collect_violations",
    "compute_residuals",
    "hamming_matrix",
    "leaf_map",
    "neighbor_joining",
    "parse_fasta",
    "patristic_distances",
    "to_newick",
    "validate_distance_matrix",
]
