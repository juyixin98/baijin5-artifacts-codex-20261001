"""FASTA parsing for aligned synthetic sequences.

The parser is deliberately strict: this backend consumes *alignments*, so
all sequences must be the same length and contain only IUPAC DNA symbols
plus the configured gap symbol. Failures raise typed errors with the
offending record/line so tests can assert on failure categories.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import AlignmentShapeError, FastaParseError, InvalidResidueError

# IUPAC degenerate DNA symbols -> the concrete bases they stand for.
IUPAC_DNA: dict[str, tuple[str, ...]] = {
    "A": ("A",),
    "C": ("C",),
    "G": ("G",),
    "T": ("T",),
    "R": ("A", "G"),
    "Y": ("C", "T"),
    "S": ("G", "C"),
    "W": ("A", "T"),
    "K": ("G", "T"),
    "M": ("A", "C"),
    "B": ("C", "G", "T"),
    "D": ("A", "G", "T"),
    "H": ("A", "C", "T"),
    "V": ("A", "C", "G"),
    "N": ("A", "C", "G", "T"),
}


@dataclass(frozen=True)
class Alignment:
    """A rectangular multiple sequence alignment."""

    sequence_ids: tuple[str, ...]
    rows: tuple[str, ...]  # aligned rows, all equal length, upper-case

    @property
    def n_sequences(self) -> int:
        return len(self.rows)

    @property
    def n_columns(self) -> int:
        return len(self.rows[0]) if self.rows else 0


def parse_fasta_alignment(text: str, gap_symbol: str = "-") -> Alignment:
    """Parse FASTA text into an :class:`Alignment`, validating shape and alphabet."""
    if not text or not text.strip():
        raise FastaParseError("empty input: expected FASTA text")

    ids: list[str] = []
    rows: list[str] = []
    current_id: str | None = None
    current_chunks: list[str] = []

    def flush() -> None:
        if current_id is None:
            return
        if not current_chunks:
            raise FastaParseError(f"record {current_id!r} has no sequence data")
        ids.append(current_id)
        rows.append("".join(current_chunks).upper())

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            header = line[1:].strip()
            if not header:
                raise FastaParseError(f"line {lineno}: empty FASTA header")
            current_id = header.split()[0]
            current_chunks = []
        else:
            if current_id is None:
                raise FastaParseError(
                    f"line {lineno}: sequence data before first header"
                )
            current_chunks.append(line)
    flush()

    if len(ids) < 2:
        raise AlignmentShapeError(
            f"need at least 2 sequences for an alignment, got {len(ids)}"
        )
    if len(set(ids)) != len(ids):
        raise FastaParseError("duplicate sequence identifiers in FASTA input")

    lengths = {len(row) for row in rows}
    if len(lengths) != 1:
        raise AlignmentShapeError(
            f"alignment is not rectangular: row lengths {sorted(lengths)}"
        )

    allowed = set(IUPAC_DNA) | {gap_symbol}
    for seq_id, row in zip(ids, rows):
        for pos, char in enumerate(row, start=1):
            if char not in allowed:
                raise InvalidResidueError(
                    f"sequence {seq_id!r} column {pos}: unsupported symbol {char!r}"
                )

    return Alignment(sequence_ids=tuple(ids), rows=tuple(rows))
