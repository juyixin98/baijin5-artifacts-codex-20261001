"""Parsing of the synthetic alignment fixture format and CIGAR subset.

Fixture format (TSV, one alignment per line, '#' comments allowed):

    read_id  ref  start  mapq  flags  cigar

- start is 0-based (leftmost mapped reference position).
- mapq is a non-negative integer or '*' for unknown.
- cigar supports M, =, X (covered), D, N (reference gap, NOT covered),
  I, S (query-only), H (hard clip).

`cigar_to_blocks` walks the reference and returns only the covered blocks;
gap operations advance the reference cursor without emitting coverage.
"""

from __future__ import annotations

import re

from .models import AlignmentRecord, Block

COVERED_OPS = frozenset("M=X")
GAP_OPS = frozenset("DN")
QUERY_ONLY_OPS = frozenset("IS")
CLIP_OPS = frozenset("H")
_ALL_OPS = COVERED_OPS | GAP_OPS | QUERY_ONLY_OPS | CLIP_OPS

_CIGAR_TOKEN = re.compile(r"(\d+)([A-Za-z])")


class CigarError(ValueError):
    pass


class LineParseError(ValueError):
    pass


def parse_cigar(cigar: str) -> list[tuple[int, str]]:
    """Tokenize a CIGAR string into (length, op) pairs."""
    if not cigar or cigar == "*":
        raise CigarError("missing CIGAR")
    tokens: list[tuple[int, str]] = []
    pos = 0
    for match in _CIGAR_TOKEN.finditer(cigar):
        if match.start() != pos:
            raise CigarError(f"malformed CIGAR near position {pos}: {cigar!r}")
        length = int(match.group(1))
        op = match.group(2)  # case-sensitive, like SAM: 'm' is not 'M'
        if op not in _ALL_OPS:
            raise CigarError(f"unsupported CIGAR op {op!r}")
        if length == 0:
            raise CigarError("zero-length CIGAR operation")
        tokens.append((length, op))
        pos = match.end()
    if pos != len(cigar) or not tokens:
        raise CigarError(f"malformed CIGAR: {cigar!r}")
    return tokens


def cigar_to_blocks(cigar: str, start: int) -> list[Block]:
    """Convert a CIGAR into covered reference blocks starting at `start`.

    Gap operations (D/N) advance the reference cursor but emit nothing, so a
    read like 10M5N10M yields two blocks with a 5-base uncovered gap between
    them. Adjacent covered operations coalesce into a single block.
    """
    blocks: list[Block] = []
    cursor = start
    open_start: int | None = None
    for length, op in parse_cigar(cigar):
        if op in COVERED_OPS:
            if open_start is None:
                open_start = cursor
            cursor += length
        elif op in GAP_OPS:
            if open_start is not None:
                blocks.append(Block(open_start, cursor))
                open_start = None
            cursor += length
        elif op in QUERY_ONLY_OPS or op in CLIP_OPS:
            continue  # consumes query (or nothing); reference cursor unchanged
    if open_start is not None:
        blocks.append(Block(open_start, cursor))
    return blocks


def parse_alignment_line(line: str, record_index: int) -> AlignmentRecord:
    """Parse one TSV fixture line into an AlignmentRecord."""
    fields = line.rstrip("\n").split("\t")
    if len(fields) != 6:
        raise LineParseError(f"expected 6 tab-separated fields, got {len(fields)}")
    read_id, ref, start_s, mapq_s, flags_s, cigar = fields
    if not read_id:
        raise LineParseError("empty read_id")
    if not ref:
        raise LineParseError("empty reference name")
    try:
        start = int(start_s)
    except ValueError as exc:
        raise LineParseError(f"non-integer start: {start_s!r}") from exc
    if start < 0:
        raise LineParseError(f"negative start: {start}")
    if mapq_s == "*":
        mapq: int | None = None
    else:
        try:
            mapq = int(mapq_s)
        except ValueError as exc:
            raise LineParseError(f"non-integer mapq: {mapq_s!r}") from exc
        if not 0 <= mapq <= 255:
            raise LineParseError(f"mapq out of range [0,255]: {mapq}")
    try:
        flags = int(flags_s, 0)  # accepts decimal and 0x-prefixed hex
    except ValueError as exc:
        raise LineParseError(f"non-integer flags: {flags_s!r}") from exc
    if flags < 0:
        raise LineParseError(f"negative flags: {flags}")
    return AlignmentRecord(
        record_index=record_index,
        read_id=read_id,
        ref=ref,
        start=start,
        mapq=mapq,
        flags=flags,
        cigar=cigar,
    )


def iter_alignment_lines(text: str):
    """Yield (record_index, line) for non-comment, non-blank fixture lines."""
    index = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield index, raw
        index += 1
