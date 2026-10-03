"""Synthetic sequence parsing and alphabet validation.

Accepted alphabet (case-insensitive, stored upper-case):
    A C G T            unambiguous bases
    R Y W S K M B D H V N   IUPAC ambiguity codes (excluded from comparison)
    -                  alignment gap (excluded from comparison)

Any other character is a hard input error. Parsing never silently drops or
rewrites characters; exclusion decisions happen in distance.classify_sites.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import InputValidationError

UNAMBIGUOUS_BASES = frozenset("ACGT")
AMBIGUOUS_BASES = frozenset("RYWSKMBDHVN")
GAP_CHARS = frozenset("-.")
VALID_ALPHABET = UNAMBIGUOUS_BASES | AMBIGUOUS_BASES | GAP_CHARS


@dataclass(frozen=True)
class ParsedSequences:
    """Two aligned sequences ready for site classification."""

    seq1: str
    seq2: str

    @property
    def alignment_length(self) -> int:
        return len(self.seq1)


def validate_sequence(raw: str, *, label: str) -> str:
    """Upper-case and validate one sequence against the accepted alphabet."""
    if not isinstance(raw, str) or not raw.strip():
        raise InputValidationError(
            f"{label} must be a non-empty string",
            detail={"label": label},
        )
    seq = raw.strip().upper()
    bad = sorted({c for c in seq if c not in VALID_ALPHABET})
    if bad:
        raise InputValidationError(
            f"{label} contains characters outside the accepted alphabet",
            detail={"label": label, "invalid_characters": bad},
        )
    return seq


def parse_pair(seq1: str, seq2: str) -> ParsedSequences:
    """Validate a pair of already-aligned sequences."""
    s1 = validate_sequence(seq1, label="seq1")
    s2 = validate_sequence(seq2, label="seq2")
    if len(s1) != len(s2):
        raise InputValidationError(
            "aligned sequences must have equal length",
            detail={"len_seq1": len(s1), "len_seq2": len(s2)},
        )
    return ParsedSequences(seq1=s1, seq2=s2)


def parse_fasta(text: str) -> list[tuple[str, str]]:
    """Parse a minimal FASTA fixture into (name, sequence) records.

    Deliberately strict: every record needs a header and at least one
    sequence line; sequence lines must pass the alphabet check.
    """
    records: list[tuple[str, list[str]]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            name = line[1:].strip()
            if not name:
                raise InputValidationError(
                    "FASTA header without a name",
                    detail={"line": lineno},
                )
            records.append((name, []))
        else:
            if not records:
                raise InputValidationError(
                    "FASTA sequence data before any header",
                    detail={"line": lineno},
                )
            records[-1][1].append(line)
    if not records:
        raise InputValidationError("FASTA input contains no records")
    out: list[tuple[str, str]] = []
    for name, chunks in records:
        if not chunks:
            raise InputValidationError(
                "FASTA record without sequence data",
                detail={"record": name},
            )
        out.append((name, validate_sequence("".join(chunks), label=name)))
    return out


def parse_fasta_pair(text: str) -> ParsedSequences:
    """Parse a FASTA fixture holding exactly two aligned records."""
    records = parse_fasta(text)
    if len(records) != 2:
        raise InputValidationError(
            "FASTA input must contain exactly two records",
            detail={"n_records": len(records)},
        )
    return parse_pair(records[0][1], records[1][1])
