"""Domain model: alignment records, covered blocks, decisions, results.

All intervals are 0-based half-open [start, end). A `Block` is a maximal
covered reference interval contributed by one read; gap operations (D/N)
split blocks and never appear as blocks themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import DecisionStatus, ReasonCode


@dataclass(frozen=True)
class AlignmentRecord:
    """One parsed alignment line before filtering."""

    record_index: int          # 0-based position in the input stream
    read_id: str
    ref: str
    start: int                 # 0-based leftmost mapped position
    mapq: int | None           # None = declared unknown ("*" in fixtures)
    flags: int
    cigar: str


@dataclass(frozen=True)
class Block:
    """A covered reference interval [start, end)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"empty or inverted block: [{self.start}, {self.end})")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class ReadBlock:
    """A covered block attributed to a read (post-filter, pre-dedup)."""

    read_id: str
    ref: str
    start: int
    end: int


@dataclass(frozen=True)
class Decision:
    """Auditable per-record verdict with the state that produced it."""

    record_index: int
    read_id: str
    status: DecisionStatus
    reason: ReasonCode
    detail: str = ""


@dataclass(frozen=True)
class Segment:
    """A maximal reference interval of constant depth."""

    start: int
    end: int
    depth: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class CoverageResult:
    """Aggregate output of one run."""

    ref: str
    ref_length: int
    segments: tuple[Segment, ...]
    histogram: dict[int, int]   # depth -> number of reference bases at that depth
    covered_bases: int          # bases with depth >= 1
    weighted_bases: int         # sum(depth * length) over segments
    mean_depth: float
    decisions: tuple[Decision, ...] = field(default_factory=tuple)
