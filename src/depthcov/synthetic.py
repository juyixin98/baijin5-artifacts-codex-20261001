"""Deterministic synthetic fixtures: small local references and alignments.

Nothing here reads real data. The headline fixture is a tiny reference where
every per-base answer can be verified by hand.
"""

from __future__ import annotations

from .models import Alignment, Strand

# ---------------------------------------------------------------------------
# Tiny reference, hand-checkable. 10 bases, 0-based coordinates [0,10).
#
#   r1  (5M, gappy path overall): 3M2D2M at start 1 -> blocks [1,4) [6,8)
#   r2  abuts r1's first block:    2M  at start 4     -> block  [4,6)
#   r3  overlaps both:            4M  at start 3      -> block  [3,7)
#   r4  exact duplicate of r3 (same qname)            -> union, no double count
#   r5  flagged PCR duplicate                         -> rejected
#   r6  MAPQ 1                                       -> rejected (low_mapq)
#   r7  CIGAR 3M running off the end (start 9)        -> rejected (oob)
# ---------------------------------------------------------------------------

TINY_REFERENCES: dict[str, int] = {"chrTiny": 10}


def tiny_alignments() -> list[Alignment]:
    return [
        Alignment("r1", "chrTiny", 1, "3M2D2M", mapq=60),
        Alignment("r2", "chrTiny", 4, "2M", mapq=60),
        Alignment("r3", "chrTiny", 3, "4M", mapq=60),
        Alignment("r3", "chrTiny", 3, "4M", mapq=60),  # identical qname+interval
        Alignment("r5", "chrTiny", 0, "3M", mapq=60, is_duplicate=True),
        Alignment("r6", "chrTiny", 0, "2M", mapq=1),
        Alignment("r7", "chrTiny", 9, "3M", mapq=60),
    ]


# Hand-derived expected per-base depth on chrTiny (union-per-query policy):
# pos:  0 1 2 3 4 5 6 7 8 9
# r1:     # # #       # #      blocks [1,4) and [6,8); gap bases 4,5 empty
# r2:           # #            [4,6)
# r3:         # # # #          [3,7)
# dep:  0 1 1 2 2 2 2 1 0 0
TINY_EXPECTED_DEPTH = [0, 1, 1, 2, 2, 2, 2, 1, 0, 0]


def paired_overlap_alignments() -> list[Alignment]:
    """Two mates sharing a QNAME and overlapping: must NOT double-count."""
    return [
        Alignment("pair1", "chrTiny", 0, "6M", mapq=60),   # [0,6)
        Alignment("pair1", "chrTiny", 4, "5M", mapq=60),   # [4,9)
    ]


# Union of pair1 mates -> [0,9), depth exactly 1 everywhere in [0,9).
PAIRED_EXPECTED_DEPTH = [1, 1, 1, 1, 1, 1, 1, 1, 1, 0]


def boundary_alignments() -> list[Alignment]:
    """Abutting [0,5) and [5,10): boundary base 5 covered by the second only."""
    return [
        Alignment("b1", "chrTiny", 0, "5M", mapq=60),
        Alignment("b2", "chrTiny", 5, "5M", mapq=60),
    ]


BOUNDARY_EXPECTED_DEPTH = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]


def synthetic_random_alignments(
    ref_name: str,
    ref_length: int,
    n: int,
    *,
    seed: int = 1234,
    max_len: int = 12,
    gap_prob: float = 0.15,
) -> list[Alignment]:
    """Generate deterministic random alignments, some with D/N gaps.

    Imported lazily so the package does not require a RNG for the tiny suite.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    out: list[Alignment] = []
    for i in range(n):
        length = int(rng.integers(2, max_len + 1))
        start = int(rng.integers(0, max(1, ref_length - 1)))
        # Trim to fit reference.
        length = min(length, ref_length - start)
        if length <= 0:
            continue
        cigar = f"{length}M"
        if length >= 4 and rng.random() < gap_prob:
            a = max(1, length // 2 - 1)
            gap = int(rng.integers(1, 3))
            b = length - a
            cigar = f"{a}M{gap}D{b}M"
            # Drop if footprint exceeds reference.
            if start + a + gap + b > ref_length:
                cigar = f"{length}M"
        mapq = int(rng.choice([0, 10, 20, 30, 60]))
        strand = Strand.FORWARD if rng.random() < 0.5 else Strand.REVERSE
        out.append(
            Alignment(
                f"syn{i:05d}", ref_name, start, cigar,
                mapq=mapq, strand=strand,
            )
        )
    return out
