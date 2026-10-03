"""Independent brute-force oracle used ONLY by tests.

This is deliberately written from scratch (manual CIGAR character scanner,
set-based per-base accumulation) and shares no code with the production
engine, so agreement is evidence rather than self-confirmation.
"""

from __future__ import annotations

from depthcov.models import Alignment

COVER = set("M=X")
GAP = set("DN")
QUERY_ONLY = set("IS")
NOCOORD = set("HP")
VALID_OPS = COVER | GAP | QUERY_ONLY | NOCOORD


class OracleCigarError(ValueError):
    pass


def oracle_walk(start: int, cigar: str) -> tuple[list[int], int]:
    """Return (covered positions, ref_end) via a hand-written scanner."""
    if not cigar:
        raise OracleCigarError("empty")
    positions: list[int] = []
    ref = start
    i = 0
    n = len(cigar)
    saw = False
    while i < n:
        j = i
        while j < n and cigar[j].isdigit():
            j += 1
        if j == i or j == n:
            raise OracleCigarError(f"bad token at {i}: {cigar[i:]!r}")
        length = int(cigar[i:j])
        if length == 0:
            raise OracleCigarError("zero length")
        op = cigar[j]
        if op not in VALID_OPS:
            raise OracleCigarError(f"bad op {op!r}")
        for _ in range(length):
            if op in COVER:
                positions.append(ref)
                ref += 1
            elif op in GAP:
                ref += 1
        saw = True
        i = j + 1
    if not saw:
        raise OracleCigarError("no ops")
    return positions, ref


def oracle_accepted(
    alignments: list[Alignment],
    refs: dict[str, int],
    *,
    min_mapq: int = 20,
    reject_dupes: bool = True,
) -> tuple[dict[str, Alignment], list[tuple[Alignment, str]]]:
    """Independent filtering. Returns (accepted, rejected-with-reason)."""
    accepted: dict[str, Alignment] = {}
    rejected: list[tuple[Alignment, str]] = []
    for aln in alignments:
        if aln.ref_name not in refs:
            rejected.append((aln, "unknown_reference"))
            continue
        if reject_dupes and aln.is_duplicate:
            rejected.append((aln, "duplicate"))
            continue
        if aln.mapq < min_mapq:
            rejected.append((aln, "low_mapq"))
            continue
        try:
            positions, ref_end = oracle_walk(aln.ref_start, aln.cigar)
        except OracleCigarError:
            rejected.append((aln, "invalid_cigar"))
            continue
        if ref_end > refs[aln.ref_name]:
            rejected.append((aln, "out_of_bounds"))
            continue
        if not positions:
            rejected.append((aln, "no_covered_bases"))
            continue
        accepted[id(aln)] = aln
    return accepted, rejected


def oracle_depth(
    alignments: list[Alignment],
    refs: dict[str, int],
    *,
    min_mapq: int = 20,
    reject_dupes: bool = True,
    union_per_query: bool = True,
) -> dict[str, list[int]]:
    """Brute-force per-base depth using plain Python sets."""
    accepted, _ = oracle_accepted(
        alignments, refs, min_mapq=min_mapq, reject_dupes=reject_dupes
    )
    depths = {name: [0] * length for name, length in refs.items()}
    if union_per_query:
        covered: dict[tuple[str, str], set[int]] = {}
        for aln in accepted.values():
            positions, _ = oracle_walk(aln.ref_start, aln.cigar)
            key = (aln.ref_name, aln.query_name)
            covered.setdefault(key, set()).update(positions)
        for (ref_name, _qname), positions in covered.items():
            for p in positions:
                depths[ref_name][p] += 1
    else:
        for aln in accepted.values():
            positions, _ = oracle_walk(aln.ref_start, aln.cigar)
            for p in positions:
                depths[aln.ref_name][p] += 1
    return depths


def oracle_weighted_length(depth: list[int]) -> int:
    return sum(depth)
