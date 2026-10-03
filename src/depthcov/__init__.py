"""depthcov: per-segment coverage depth with quality filtering."""

from .config import Settings
from .coverage import (
    PER_RECORD,
    UNION_PER_QUERY,
    DepthResult,
    DepthSegment,
    coverage_for_reference,
    merge_intervals,
)
from .engine import Reference, RunReport, analyze, analyze_lines
from .filtering import FilterConfig, adjudicate
from .models import Alignment, RejectReason, Strand, Verdict

__all__ = [
    "Settings",
    "Alignment",
    "Strand",
    "Verdict",
    "RejectReason",
    "Reference",
    "FilterConfig",
    "adjudicate",
    "coverage_for_reference",
    "merge_intervals",
    "DepthResult",
    "DepthSegment",
    "RunReport",
    "analyze",
    "analyze_lines",
    "UNION_PER_QUERY",
    "PER_RECORD",
]

__version__ = "0.1.0"
