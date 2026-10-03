"""Synthetic sequence parsing and validation.

Accepts raw sequences (whitespace-tolerant, case-insensitive) and FASTA
text. Only A/C/G/T and the ambiguity base N are admitted; anything else is
a declared ``invalid_character`` failure with the offending position.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import BASES, UNKNOWN_BASE
from .errors import EmptySequenceError, FastaFormatError, InvalidCharacterError

_VALID = frozenset(BASES) | {UNKNOWN_BASE}


@dataclass(frozen=True)
class ParsedSequence:
    id: str
    sequence: str  # uppercase, A/C/G/T/N only

    @property
    def length(self) -> int:
        return len(self.sequence)


def parse_sequence(seq_id: str, raw: str) -> ParsedSequence:
    """Normalize and validate one raw sequence string."""
    seq = "".join(raw.split()).upper()
    if not seq:
        raise EmptySequenceError(
            f"sequence {seq_id!r} is empty after stripping whitespace",
            {"seq_id": seq_id},
        )
    for pos, ch in enumerate(seq):
        if ch not in _VALID:
            raise InvalidCharacterError(
                f"invalid character {ch!r} at position {pos} of sequence "
                f"{seq_id!r}; only A/C/G/T and N (unknown) are supported",
                {"seq_id": seq_id, "position": pos, "character": ch},
            )
    return ParsedSequence(id=seq_id, sequence=seq)


def parse_fasta(text: str) -> list[ParsedSequence]:
    """Parse (possibly multi-record) FASTA text into validated sequences."""
    records: list[tuple[str, list[str]]] = []
    current_id: str | None = None
    buf: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None:
                records.append((current_id, buf))
            header = line[1:].split()
            current_id = header[0] if header else f"seq{len(records) + 1}"
            buf = []
        else:
            if current_id is None:
                raise FastaFormatError(
                    f"sequence data before first FASTA header at line {lineno}",
                    {"line": lineno},
                )
            buf.append(line)
    if current_id is not None:
        records.append((current_id, buf))
    if not records:
        raise EmptySequenceError("no FASTA records found", {})
    return [parse_sequence(seq_id, "".join(chunks)) for seq_id, chunks in records]
