"""Domain types for coverage-depth computation.

Coordinate convention (fixed across the whole package):

* All coordinates are 0-based, half-open intervals ``[start, end)``.
* A single-base feature at reference position ``p`` is ``[p, p + 1)``.
* Consumes (``M``/``=`/``X``) advance BOTH query and reference and count.
* Reference skips (``D``/``N``) advance reference only, NOT query; the bases
  they span are a GAP in the alignment and never accumulate coverage.
* Insertions (``I``) advance query only, never reference (no coverage).
* Soft/hard clips and padding advance neither reference coordinate used for
  coverage (soft clips advance query but are not aligned; hard clips/padding
  do not even consume query bases in the SEQ field).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: CIGAR operations that consume reference bases and COUNT as coverage.
CONSUMES_OPS = frozenset({"M", "=", "X"})
#: Reference-consuming operations that are a GAP (never count as coverage).
GAP_OPS = frozenset({"D", "N"})
#: Query-consuming operations that do not touch the reference (no coverage).
QUERY_INSERT_OPS = frozenset({"I", "S"})
#: Operations consuming neither coordinate for alignment purposes.
NO_REF_OPS = frozenset({"H", "P"})

#: Every CIGAR op we accept.
KNOWN_OPS = CONSUMES_OPS | GAP_OPS | QUERY_INSERT_OPS | NO_REF_OPS


class Strand(str, Enum):
    FORWARD = "+"
    REVERSE = "-"


@dataclass(frozen=True)
class Alignment:
    """One aligned query record (a synthetic SAM-like row).

    ``cigar`` is a standard CIGAR string, e.g. ``"5M2D3M"``. The covered
    reference footprint is ``[ref_start, ref_end)`` *minus* any internal
    D/N gaps; ``ref_end`` is derived from CIGAR reference consumption.
    """

    query_name: str
    ref_name: str
    ref_start: int
    cigar: str
    mapq: int = 60
    query_length: int | None = None
    strand: Strand = Strand.FORWARD
    read_group: str = "default"
    # Synthetic analogue of SAM FLAG 0x400 (PCR/optical duplicate).
    is_duplicate: bool = False

    def __post_init__(self) -> None:
        if not self.query_name:
            raise ValueError("query_name must be non-empty")
        if not self.ref_name:
            raise ValueError("ref_name must be non-empty")
        if self.ref_start < 0:
            raise ValueError(f"ref_start must be >= 0, got {self.ref_start}")
        if self.mapq < 0:
            raise ValueError(f"mapq must be >= 0, got {self.mapq}")


@dataclass(frozen=True)
class CoveredBlock:
    """A gap-free covered piece of one alignment: [start, end)."""

    start: int
    end: int
    query_name: str
    mapq: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class DepthSegment:
    """A maximal reference span with constant coverage depth.

    ``[start, end)`` carries ``depth`` overlapping (de-duplicated) reads.
    """

    start: int
    end: int
    depth: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class DepthResult:
    """Result for one reference: per-base depth and derived aggregates."""

    ref_name: str
    ref_length: int
    # Per-base depth array, len == ref_length (dtype int64).
    per_base_depth: object
    segments: tuple[DepthSegment, ...]
    # Histogram: count of BASES observed at each depth.
    # histogram[d] == number of reference bases covered by exactly d reads.
    histogram: dict[int, int]
    # Sum over blocks of (length * depth) == sum of covered query bases
    # contributed by every ACCEPTED alignment, gaps excluded.
    weighted_length: int
    # Number of bases covered by >= 1 read.
    covered_bases: int
    accepted_queries: tuple[str, ...] = field(default_factory=tuple)


class RejectReason(str, Enum):
    """Why an alignment could not contribute coverage."""

    LOW_MAPQ = "low_mapq"
    OUT_OF_BOUNDS = "out_of_bounds"
    INVALID_CIGAR = "invalid_cigar"
    INVALID_INTERVAL = "invalid_interval"
    UNKNOWN_REFERENCE = "unknown_reference"
    NO_COVERED_BASES = "no_covered_bases"
    DUPLICATE = "duplicate"
    MALFORMED_RECORD = "malformed_record"


@dataclass(frozen=True)
class Verdict:
    """An accept/reject/undetermined decision with full rationale."""

    query_name: str
    accepted: bool
    reason: str
    detail: str
    ref_name: str | None = None
    ref_start: int | None = None
    ref_end: int | None = None
    mapq: int | None = None
    blocks: tuple[CoveredBlock, ...] = ()
