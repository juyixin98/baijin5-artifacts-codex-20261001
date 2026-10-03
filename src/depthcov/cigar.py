"""CIGAR parsing and gap-aware expansion into covered reference blocks.

Pure functions, no I/O. A reference answer (``naive_covered_positions``)
is intentionally provided here in the simplest possible form so tests can
cross-check the block expansion independently of the sweep-line engine.
"""

from __future__ import annotations

import re

from .models import Alignment, CoveredBlock

# "<positive integer><op letter>", e.g. "10M2N5M".
_CIGAR_TOKEN_RE = re.compile(r"(\d+)([MIDNSHP=X])")


class CigarError(ValueError):
    """Raised for a malformed CIGAR string."""


def parse_cigar(cigar: str) -> list[tuple[int, str]]:
    """Parse a CIGAR string into ``[(length, op), ...]``.

    Raises :class:`CigarError` on empty input, zero length, unknown op,
    or any characters left unconsumed (malformed string).
    """
    if not cigar:
        raise CigarError("empty CIGAR")
    tokens: list[tuple[int, str]] = []
    pos = 0
    for match in _CIGAR_TOKEN_RE.finditer(cigar):
        if match.start() != pos:
            raise CigarError(
                f"malformed CIGAR near offset {pos}: {cigar[pos:]!r}"
            )
        length = int(match.group(1))
        op = match.group(2)
        if length == 0:
            raise CigarError(f"zero-length {op} op in {cigar!r}")
        tokens.append((length, op))
        pos = match.end()
    if pos != len(cigar):
        raise CigarError(f"malformed CIGAR tail: {cigar[pos:]!r}")
    if not tokens:
        raise CigarError(f"no CIGAR operations parsed from {cigar!r}")
    return tokens


def reference_span(cigar: str) -> int:
    """Number of reference bases the CIGAR consumes (gaps included)."""
    from .models import GAP_OPS, CONSUMES_OPS

    return sum(
        length
        for length, op in parse_cigar(cigar)
        if op in CONSUMES_OPS or op in GAP_OPS
    )


def query_span(cigar: str) -> int:
    """Number of query bases the CIGAR consumes (clips included)."""
    from .models import CONSUMES_OPS, QUERY_INSERT_OPS

    return sum(
        length
        for length, op in parse_cigar(cigar)
        if op in CONSUMES_OPS or op in QUERY_INSERT_OPS
    )


def covered_blocks(
    ref_start: int, cigar: str
) -> tuple[list[CoveredBlock], int]:
    """Expand a CIGAR into gap-free covered blocks.

    Returns ``(blocks, ref_end)`` where every block is ``[s, e)`` on the
    reference and D/N runs split (and are excluded from) the blocks.
    The blocks carry an empty ``query_name``; the caller annotates them.
    """
    from .models import CONSUMES_OPS, GAP_OPS

    tokens = parse_cigar(cigar)
    ref_pos = ref_start
    blocks: list[CoveredBlock] = []
    run_start: int | None = None

    def _close(run_end: int) -> None:
        nonlocal run_start
        if run_start is not None and run_end > run_start:
            blocks.append(CoveredBlock(run_start, run_end, "", 0))
        run_start = None

    for length, op in tokens:
        if op in CONSUMES_OPS:
            if run_start is None:
                run_start = ref_pos
            ref_pos += length
        elif op in GAP_OPS:
            # A D/N gap: it consumes reference but must NOT count. Close
            # the current covered run before the gap, skip over the gap.
            _close(ref_pos)
            ref_pos += length
        else:
            # I/S/H/P: no reference coordinate.
            continue
    _close(ref_pos)
    return blocks, ref_pos


def blocks_for_alignment(
    alignment: Alignment,
) -> tuple[list[CoveredBlock], int]:
    """Return annotated blocks and derived ref_end for an alignment."""
    blocks, ref_end = covered_blocks(alignment.ref_start, alignment.cigar)
    annotated = [
        CoveredBlock(b.start, b.end, alignment.query_name, alignment.mapq)
        for b in blocks
    ]
    return annotated, ref_end


def naive_covered_positions(ref_start: int, cigar: str) -> list[int]:
    """Reference oracle: list every covered 0-based reference position.

    This walks the CIGAR one base at a time and appends a position only
    for M/=/X. It is deliberately the most obvious correct implementation
    and is used by tests as an independent cross-check, never as the
    production engine.
    """
    from .models import CONSUMES_OPS, GAP_OPS

    positions: list[int] = []
    ref_pos = ref_start
    for length, op in parse_cigar(cigar):
        for _ in range(length):
            if op in CONSUMES_OPS:
                positions.append(ref_pos)
                ref_pos += 1
            elif op in GAP_OPS:
                ref_pos += 1
            # query-only / no-coordinate ops: nothing
    return positions
