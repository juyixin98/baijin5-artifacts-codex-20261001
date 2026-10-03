"""Independent reference oracle.

This is deliberately written from the problem STATEMENT, not from the
implementation under test: no numpy, no searchsorted, no reuse of
txmap.mapping. It walks every base with plain ``range`` objects. Tests use it
to cross-check the vectorized core, so the expected answers are not generated
by the code being validated.

Convention restated:
  * both systems 0-based, half-open
  * "+" tx walks exons ascending, bases unchanged
  * "-" tx walks exons descending, each genomic position decreasing by one,
    transcript base = complement(reference base)
"""

from __future__ import annotations

_COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C"}


def build_point_maps(exons: list[tuple[int, int]], strand: str):
    """Return (tx2g, g2tx) for every mature base."""
    ordered = exons if strand == "+" else list(reversed(exons))
    tx2g: list[int] = []
    for start, end in ordered:
        walk = range(start, end) if strand == "+" else range(end - 1, start - 1, -1)
        tx2g.extend(walk)
    g2tx = {g: t for t, g in enumerate(tx2g)}
    return tx2g, g2tx


def classify_genomic(exons, g: int) -> str:
    """'exon' | 'intron' | 'outside' for a single genomic position."""
    lo, hi = exons[0][0], exons[-1][1]
    if g < lo or g >= hi:
        return "outside"
    for s, e in exons:
        if s <= g < e:
            return "exon"
    return "intron"


def tx_base(strand: str, pattern: str, genomic_position: int) -> str:
    ref = pattern[genomic_position % len(pattern)]
    return ref if strand == "+" else _COMPLEMENT[ref]
