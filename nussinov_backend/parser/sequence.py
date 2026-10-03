"""Synthetic RNA sequence parsing and validation.

This module is the single input boundary of the system. It normalizes
sequences from local synthetic fixtures (plain text / FASTA-like strings)
and rejects anything that is not a valid RNA alphabet string.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import (
    EmptySequenceError,
    InvalidBaseError,
    SequenceTooLongError,
)

RNA_ALPHABET = frozenset("ACGU")
# Letters accepted on input after normalization (T -> U, DNA style fixtures).
INPUT_ALPHABET = frozenset("ACGUT")

FASTA_HEADER_PREFIX = ">"
FASTA_COMMENT_PREFIX = ";"


@dataclass(frozen=True)
class ParsedSequence:
    """A normalized RNA sequence ready for the domain layer."""

    raw: str
    sequence: str
    length: int
    source: str
    fasta_header: str | None = None
    normalized_bases: tuple[tuple[int, str, str], ...] = ()


def _strip_fasta(text: str) -> tuple[str, str | None]:
    """Return (concatenated body, first header) from a FASTA-like string."""
    header: str | None = None
    body_parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(FASTA_HEADER_PREFIX):
            if header is None:
                header = stripped[1:].strip()
            continue
        if stripped.startswith(FASTA_COMMENT_PREFIX):
            continue
        body_parts.append(stripped)
    return "".join(body_parts), header


def parse_sequence(
    raw: object,
    *,
    max_length: int,
    source: str = "api",
) -> ParsedSequence:
    """Validate and normalize a raw sequence value.

    Accepts plain RNA/DNA strings or FASTA-like text. Whitespace and line
    breaks are removed, input is upper-cased, and DNA ``T`` is converted to
    RNA ``U``.

    Raises a typed subclass of :class:`SequenceValidationError` on failure.
    """
    if not isinstance(raw, str):
        raise InvalidBaseError(
            f"sequence must be a string, got {type(raw).__name__}",
            position=None,
            base=None,
        )

    text = raw.strip()
    fasta_header: str | None = None
    if FASTA_HEADER_PREFIX in text or FASTA_COMMENT_PREFIX in text:
        text, fasta_header = _strip_fasta(text)
        source = f"{source}:fasta"

    # Remove internal whitespace (e.g. "G G A U").
    compact = "".join(text.split()).upper()

    if not compact:
        raise EmptySequenceError("sequence is empty after normalization")

    if len(compact) > max_length:
        raise SequenceTooLongError(
            f"sequence length {len(compact)} exceeds configured maximum {max_length}",
            length=len(compact),
            max_length=max_length,
        )

    normalized_chars: list[str] = []
    changes: list[tuple[int, str, str]] = []
    for index, base in enumerate(compact):
        if base not in INPUT_ALPHABET:
            raise InvalidBaseError(
                f"invalid base {base!r} at position {index} (1-based {index + 1}); "
                f"allowed alphabet: {''.join(sorted(INPUT_ALPHABET))}",
                position=index,
                base=base,
            )
        converted = "U" if base == "T" else base
        if converted != base:
            changes.append((index, base, converted))
        normalized_chars.append(converted)

    sequence = "".join(normalized_chars)
    return ParsedSequence(
        raw=raw if isinstance(raw, str) else "",
        sequence=sequence,
        length=len(sequence),
        source=source,
        fasta_header=fasta_header,
        normalized_bases=tuple(changes),
    )
