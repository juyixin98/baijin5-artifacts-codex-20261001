"""Synthetic sequence generation and strand-aware sequence handling.

The reference is deterministic: base(g) = pattern[g mod len(pattern)], so any
expected base is trivially hand-computable. Minus-strand transcript sequence
is the reverse complement of the plus-strand genomic sequence, spliced in
TRANSCRIPT (descending genomic) order.
"""

from __future__ import annotations

from .models import Transcript

_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def complement(bases: str) -> str:
    return bases.translate(_COMPLEMENT)


def reverse_complement(bases: str) -> str:
    return bases.translate(_COMPLEMENT)[::-1]


def genomic_subsequence(pattern: str, start: int, end: int) -> str:
    """Reference bases for half-open genomic interval [start, end)."""
    if not (0 <= start <= end):
        raise ValueError(f"bad interval [{start}, {end})")
    period = len(pattern)
    out: list[str] = []
    for g in range(start, end):
        out.append(pattern[g % period])
    return "".join(out)


def transcript_sequence(tx: Transcript, pattern: str) -> str:
    """Full mature transcript sequence with correct strand orientation.

    Plus strand: exons ascending, reference bases unchanged.
    Minus strand: each exon's bases are reverse-complemented and exons are
    concatenated in descending genomic order.
    """
    if tx.strand == "+":
        return "".join(
            genomic_subsequence(pattern, e.start, e.end) for e in tx.exons
        )
    return "".join(
        reverse_complement(genomic_subsequence(pattern, e.start, e.end))
        for e in reversed(tx.exons)
    )


def bases_at_transcript_positions(
    tx: Transcript, pattern: str, positions: list[int]
) -> str:
    """Return transcript bases at given mature-transcript positions."""
    seq = transcript_sequence(tx, pattern)
    return "".join(seq[p] for p in positions)


def bases_at_genomic_positions(
    pattern: str, positions: list[int]
) -> str:
    """Reference-strand bases at the given genomic positions."""
    return "".join(pattern[g % len(pattern)] for g in positions)
