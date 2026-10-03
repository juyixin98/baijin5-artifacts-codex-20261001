"""Immutable domain value objects.

All objects are frozen dataclasses: mapping never mutates transcript state,
which also makes identity isolation across transcripts trivially safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALID_STRANDS = ("+", "-")


@dataclass(frozen=True, slots=True)
class Exon:
    """One exon as a 0-based half-open genomic interval [start, end)."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def contains_position(self, genomic_position: int) -> bool:
        """A *position* denotes the base at that index; end itself is excluded."""
        return self.start <= genomic_position < self.end


@dataclass(frozen=True, slots=True)
class Transcript:
    transcript_id: str
    chrom: str
    strand: str  # "+" or "-"
    exons: tuple[Exon, ...] = field(default_factory=tuple)

    @property
    def length(self) -> int:
        """Mature transcript length in bases."""
        return sum(e.length for e in self.exons)

    @property
    def tx_start(self) -> int:
        return self.exons[0].start

    @property
    def tx_end(self) -> int:
        return self.exons[-1].end

    def genomic_span(self) -> tuple[int, int]:
        return self.tx_start, self.tx_end


@dataclass(frozen=True, slots=True)
class Fragment:
    """One contiguous aligned piece of a mapped interval.

    Both intervals are half-open. ``length`` is identical on both sides.
    Fragments are always returned in TRANSCRIPT order; ``genomic_order``
    records ascending genomic order for minus-strand debugging/provenance.
    """

    tx_start: int
    tx_end: int
    genomic_start: int
    genomic_end: int
    exon_index: int  # index in ascending-genomic exon list
    genomic_order: int = 0

    @property
    def length(self) -> int:
        return self.tx_end - self.tx_start


@dataclass(frozen=True, slots=True)
class PointMapping:
    """Single-base map result with provenance."""

    transcript_id: str
    strand: str
    tx_position: int
    genomic_position: int
    exon_index: int


@dataclass(frozen=True, slots=True)
class IntervalMapping:
    """Region map result; possibly several ordered fragments."""

    transcript_id: str
    strand: str
    tx_start: int
    tx_end: int
    fragments: tuple[Fragment, ...]

    @property
    def length(self) -> int:
        return self.tx_end - self.tx_start

    @property
    def mapped_length(self) -> int:
        return sum(f.length for f in self.fragments)

    @property
    def fragment_count(self) -> int:
        return len(self.fragments)
