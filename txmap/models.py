"""Domain model: contigs, transcripts, exons, mapping outcomes.

Pure data definitions with no I/O. Everything here is immutable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Tuple


class Strand(enum.Enum):
    PLUS = "+"
    MINUS = "-"

    @classmethod
    def parse(cls, raw: str) -> "Strand":
        if raw == "+":
            return cls.PLUS
        if raw == "-":
            return cls.MINUS
        raise ValueError(f"invalid strand: {raw!r}")


class Status(enum.Enum):
    """Outcome category of a mapping request."""

    OK = "OK"  # fully mapped
    PARTIAL = "PARTIAL"  # interval mapped with intronic/out-of-transcript gaps
    REJECTED = "REJECTED"  # cannot map (hard failure, deterministic reason)
    INDETERMINATE = "INDETERMINATE"  # cannot decide (missing reference data)


class Reason(enum.Enum):
    OK = "OK"
    INTRONIC = "INTRONIC"  # position falls inside an intron: no hard mapping
    OUT_OF_TRANSCRIPT = "OUT_OF_TRANSCRIPT"  # outside the transcript's exon span
    OUT_OF_CONTIG = "OUT_OF_CONTIG"  # beyond the reference contig bounds
    UNKNOWN_TRANSCRIPT = "UNKNOWN_TRANSCRIPT"
    UNKNOWN_CONTIG = "UNKNOWN_CONTIG"
    INVALID_INTERVAL = "INVALID_INTERVAL"  # start >= end or negative
    INVALID_POSITION = "INVALID_POSITION"  # negative position
    SEQUENCE_UNAVAILABLE = "SEQUENCE_UNAVAILABLE"


@dataclass(frozen=True)
class Exon:
    """One exon as a genomic half-open interval [start, end)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid exon interval [{self.start}, {self.end})")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Transcript:
    """A transcript: identity + strand + exons in genomic ascending order."""

    tx_id: str
    gene: str
    contig: str
    strand: Strand
    exons: Tuple[Exon, ...] = field(compare=False)

    def __post_init__(self) -> None:
        if not self.tx_id:
            raise ValueError("transcript id must be non-empty")
        if not self.exons:
            raise ValueError("transcript must have at least one exon")
        for prev, nxt in zip(self.exons, self.exons[1:]):
            if nxt.start <= prev.end:
                raise ValueError(
                    f"exons of {self.tx_id} overlap or are unordered: "
                    f"[{prev.start},{prev.end}) then [{nxt.start},{nxt.end})"
                )

    @property
    def tx_length(self) -> int:
        return sum(e.length for e in self.exons)

    @property
    def genomic_span(self) -> Tuple[int, int]:
        return (self.exons[0].start, self.exons[-1].end)


@dataclass(frozen=True)
class Contig:
    name: str
    sequence: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("contig name must be non-empty")
        if not self.sequence:
            raise ValueError("contig sequence must be non-empty")

    @property
    def length(self) -> int:
        return len(self.sequence)


@dataclass(frozen=True)
class Fragment:
    """One mapped block shared between genome and transcript.

    Both intervals are half-open and have equal length.
    """

    g_start: int
    g_end: int
    t_start: int
    t_end: int

    @property
    def length(self) -> int:
        return self.g_end - self.g_start

    def __post_init__(self) -> None:
        if self.g_end - self.g_start != self.t_end - self.t_start:
            raise ValueError("fragment genomic/transcript lengths differ")


@dataclass(frozen=True)
class Gap:
    """An unmappable sub-interval of a requested genomic interval."""

    g_start: int
    g_end: int
    reason: Reason


@dataclass(frozen=True)
class PointOutcome:
    """Result of mapping a single position."""

    status: Status
    reason: Reason
    transcript_id: str
    position: int  # echo of the queried position (in the query's coordinate space)
    mapped: int | None  # mapped position (in the target coordinate space), if any


@dataclass(frozen=True)
class IntervalOutcome:
    """Result of mapping an interval: ordered fragments plus skipped gaps."""

    status: Status
    reason: Reason
    transcript_id: str
    fragments: Tuple[Fragment, ...]
    gaps: Tuple[Gap, ...]

    @property
    def mapped_length(self) -> int:
        return sum(f.length for f in self.fragments)
