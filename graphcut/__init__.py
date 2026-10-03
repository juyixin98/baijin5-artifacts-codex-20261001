"""Graph-cut binary segmentation service."""

from .contracts import SegmentationSpec, build_spec, validate_pairwise
from .energy import evaluate_energy
from .service import run_segmentation

__all__ = [
    "SegmentationSpec",
    "build_spec",
    "validate_pairwise",
    "evaluate_energy",
    "run_segmentation",
]
