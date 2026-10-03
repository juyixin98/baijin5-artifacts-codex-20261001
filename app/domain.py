"""Core domain objects shared by parsing, phasing, and the pipeline.

These are plain dataclasses (not pydantic) so the algorithm layer never
depends on the transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Variant:
    index: int  # position in the request's variant list
    id: str
    chrom: str
    pos: int
    ref: str
    alt: str


@dataclass(frozen=True)
class Observation:
    """One read's allele call at one site.

    bit: 0 = reference allele, 1 = alternate allele.
    weight: phred-scaled correction cost (== quality, clamped).
    """

    site: int
    bit: int
    weight: float


@dataclass
class Fragment:
    """All usable observations contributed by one read."""

    read_id: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def sites(self) -> list[int]:
        return [obs.site for obs in self.observations]


@dataclass
class ParsedInput:
    variants: list[Variant]
    fragments: list[Fragment]
    # Reads whose allele matched neither ref nor alt (excluded from MEC,
    # reported as evidence instead of silently dropped).
    unknown_allele_observations: int
    # Observations that arrived without a quality and received the
    # configured default.
    defaulted_quality_observations: int
