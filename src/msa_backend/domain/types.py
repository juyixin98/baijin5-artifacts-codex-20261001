"""Domain result types shared across the pipeline and the API."""

from __future__ import annotations

from dataclasses import dataclass, field

# Column conservation call categories. These are the *only* statuses a
# column can carry; there is no silent "unknown -> success" mapping.
STATUS_CONSERVED = "conserved"
STATUS_VARIABLE = "variable"
STATUS_INSUFFICIENT_COVERAGE = "insufficient_coverage"

COLUMN_STATUSES = (STATUS_CONSERVED, STATUS_VARIABLE, STATUS_INSUFFICIENT_COVERAGE)


@dataclass(frozen=True)
class ColumnResult:
    column_index: int  # 1-based alignment column
    distribution: dict[str, float]  # base -> weighted frequency (sums to 1)
    entropy_bits: float
    information_content_bits: float
    effective_coverage: float  # total non-gap weight in this column
    gap_fraction: float  # gap weight / total sequence weight
    consensus: str
    status: str  # one of COLUMN_STATUSES


@dataclass(frozen=True)
class AlignmentResult:
    sequence_ids: tuple[str, ...]
    weights: dict[str, float]
    clusters: list[list[str]]
    total_weight: float
    columns: tuple[ColumnResult, ...]
    # sequence id -> list over alignment columns of 1-based original
    # coordinate (ungapped position) or None for gap cells.
    coordinate_maps: dict[str, tuple[int | None, ...]] = field(default_factory=dict)
