"""Synthetic sequence parsing and validation.

Only the ACGT alphabet is accepted. Synthetic fixtures are plain FASTA or
in-memory records; anything else is an :class:`InvalidSequenceError` with
the offending position reported, so failures are diagnosable from logs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidSequenceError

ALPHABET = frozenset("ACGT")


@dataclass(frozen=True)
class SequenceRecord:
    name: str
    sequence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", self.sequence.upper())
        self.validate()

    def validate(self) -> None:
        if not self.name:
            raise InvalidSequenceError("sequence record requires a non-empty name")
        if not self.sequence:
            raise InvalidSequenceError(
                f"sequence {self.name!r} is empty",
                detail={"name": self.name},
            )
        for pos, base in enumerate(self.sequence):
            if base not in ALPHABET:
                raise InvalidSequenceError(
                    f"sequence {self.name!r} has invalid base {base!r} at position {pos}",
                    detail={"name": self.name, "position": pos, "base": base},
                )

    def __len__(self) -> int:
        return len(self.sequence)


def parse_fasta(text: str) -> list[SequenceRecord]:
    """Parse a small synthetic FASTA fixture into records.

    Blank lines are ignored. A record starts at ``>name``; sequence lines
    are concatenated. A file with no header, or a header with no sequence,
    is a parse error — never an empty success.
    """
    records: list[SequenceRecord] = []
    name: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        nonlocal name, chunks
        if name is None:
            return
        records.append(SequenceRecord(name=name, sequence="".join(chunks)))
        name, chunks = None, []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            name = line[1:].strip()
            if not name:
                raise InvalidSequenceError(
                    f"empty FASTA header at line {lineno}",
                    detail={"line": lineno},
                )
        else:
            if name is None:
                raise InvalidSequenceError(
                    f"sequence data before first FASTA header at line {lineno}",
                    detail={"line": lineno},
                )
            chunks.append(line)
    flush()

    if not records:
        raise InvalidSequenceError("FASTA input contained no records")
    return records
