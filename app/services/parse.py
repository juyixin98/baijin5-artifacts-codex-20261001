"""Synthetic sequence parsing and validation.

The parser is deliberately strict about the distinction between:
* determinate residues (20 canonical + U)
* ambiguous residues (B/Z/J/X) - parseable, mass may be an interval
* unsupported characters - fatal ``UNSUPPORTED_RESIDUE`` with 1-based position
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.constants import AMBIGUOUS_RESIDUES, RESIDUES
from app.domain.errors import DigestError, ErrorCode
from app.domain.models import ResidueAnnotation


@dataclass(frozen=True)
class ParsedSequence:
    raw_input: str
    sequence: str
    residues: tuple[ResidueAnnotation, ...]
    ambiguous_positions: tuple[int, ...]
    unknown_positions: tuple[int, ...]

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def has_ambiguous(self) -> bool:
        return bool(self.ambiguous_positions)

    @property
    def has_unknown(self) -> bool:
        return bool(self.unknown_positions)


def normalize_sequence(raw: str) -> str:
    """Strip whitespace (incl. internal newlines) and upper-case."""
    return "".join(raw.split()).upper()


def parse_sequence(raw: str, *, max_length: int) -> ParsedSequence:
    sequence = normalize_sequence(raw)
    if not sequence:
        raise DigestError(
            ErrorCode.EMPTY_SEQUENCE,
            "sequence is empty after removing whitespace",
        )
    if len(sequence) > max_length:
        raise DigestError(
            ErrorCode.SEQUENCE_TOO_LONG,
            f"sequence length {len(sequence)} exceeds limit {max_length}",
            detail={"length": len(sequence), "limit": max_length},
        )

    annotations: list[ResidueAnnotation] = []
    ambiguous: list[int] = []
    unknown: list[int] = []

    for idx, letter in enumerate(sequence, start=1):
        if letter in RESIDUES:
            residue = RESIDUES[letter]
            annotations.append(
                ResidueAnnotation(
                    position=idx,
                    letter=letter,
                    known=True,
                    ambiguous=False,
                    mass_min=residue.residue_mass,
                    mass_max=residue.residue_mass,
                    candidates=(letter,),
                )
            )
        elif letter in AMBIGUOUS_RESIDUES:
            amb = AMBIGUOUS_RESIDUES[letter]
            ambiguous.append(idx)
            annotations.append(
                ResidueAnnotation(
                    position=idx,
                    letter=letter,
                    known=True,
                    ambiguous=True,
                    mass_min=amb.min_mass,
                    mass_max=amb.max_mass,
                    candidates=amb.candidates,
                )
            )
        else:
            unknown.append(idx)
            annotations.append(
                ResidueAnnotation(
                    position=idx,
                    letter=letter,
                    known=False,
                    ambiguous=False,
                    mass_min=0.0,
                    mass_max=0.0,
                    candidates=(),
                )
            )

    if unknown:
        first = unknown[0]
        raise DigestError(
            ErrorCode.UNSUPPORTED_RESIDUE,
            f"unsupported residue {sequence[first - 1]!r} at position {first}",
            position=first,
            residue=sequence[first - 1],
            detail={
                "unknown_positions": unknown,
                "supported": "".join(sorted(RESIDUES)) + "BZJX",
            },
        )

    return ParsedSequence(
        raw_input=raw,
        sequence=sequence,
        residues=tuple(annotations),
        ambiguous_positions=tuple(ambiguous),
        unknown_positions=(),
    )
