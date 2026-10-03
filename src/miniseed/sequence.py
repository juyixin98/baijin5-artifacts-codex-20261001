"""Synthetic sequence parsing and canonical (forward/reverse-complement) handling.

Sequences use the DNA alphabet ``ACGT`` plus:

* ``N``        -- unknown base, allowed in input but k-mers containing N are
                  skipped (they can never produce a trustworthy seed);
* lowercase    -- accepted, normalized to uppercase;
* whitespace   -- stripped, so multi-line FASTA-like bodies parse directly.

Canonical normalization keeps strand information: every k-mer is represented
by the lexicographically smaller of itself and its reverse complement, and the
orientation (``+`` if the k-mer equals its canonical form, ``-`` otherwise) is
returned alongside it.  Seeding is therefore strand-agnostic while the
reported candidate orientation still records which strand the hit implies.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ErrorCode, MiniseedError

# Two-bit codes for the deterministic k-mer hash.
_BASE_CODE = {"A": 0, "C": 1, "G": 2, "T": 3}
_COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A", "N": "N"}
_VALID = set("ACGTN")


@dataclass(frozen=True)
class ParsedSequence:
    """Result of parsing: the cleaned sequence plus provenance counters."""

    sequence: str
    raw_length: int
    skipped_non_acgtn: int   # whitespace characters removed
    header_lines: int        # FASTA-style '>' header lines skipped
    n_count: int

    @property
    def length(self) -> int:
        return len(self.sequence)


def parse_sequence(raw: str, *, name: str = "sequence") -> ParsedSequence:
    """Validate and normalize a raw input string into an ``ACGTN`` sequence.

    Accepts either a bare sequence or FASTA-like input: lines whose first
    non-space character is ``>`` are treated as headers and skipped. Raises
    :class:`MiniseedError` with category ``EMPTY_SEQUENCE`` or
    ``INVALID_CHARACTER`` -- never returns a partially parsed value.
    """
    if raw is None:
        raise MiniseedError(
            ErrorCode.EMPTY_SEQUENCE,
            f"{name} must not be null",
            context={"field": name},
        )
    cleaned_chars: list[str] = []
    skipped = 0
    header_lines = 0
    n_count = 0
    for line in raw.splitlines() or [raw]:
        if line.strip().startswith(">"):
            header_lines += 1
            continue
        for ch in line.upper():
            if ch.isspace():
                skipped += 1
                continue
            if ch not in _VALID:
                raise MiniseedError(
                    ErrorCode.INVALID_CHARACTER,
                    f"{name} contains unsupported character {ch!r}"
                    " (allowed: A, C, G, T, N and whitespace)",
                    context={"field": name, "character": ch},
                )
            if ch == "N":
                n_count += 1
            cleaned_chars.append(ch)

    cleaned = "".join(cleaned_chars)
    if not cleaned:
        raise MiniseedError(
            ErrorCode.EMPTY_SEQUENCE,
            f"{name} contains no usable bases after whitespace removal",
            context={"field": name},
        )
    return ParsedSequence(
        sequence=cleaned,
        raw_length=len(raw),
        skipped_non_acgtn=skipped,
        header_lines=header_lines,
        n_count=n_count,
    )


def reverse_complement(seq: str) -> str:
    """Return the reverse complement; ``N`` maps to ``N``."""
    return "".join(_COMPLEMENT[b] for b in reversed(seq))


@dataclass(frozen=True)
class CanonicalKmer:
    """A k-mer reduced to its strand-independent canonical form."""

    canonical: str          # lexicographic smaller of kmer / rc(kmer)
    orientation: str        # "+" if kmer == canonical else "-"
    kmer: str               # original (forward) k-mer


def canonicalize(kmer: str) -> CanonicalKmer:
    """Reduce a k-mer to canonical form, retaining strand orientation.

    Tie rule (part of the fixed hash/tie contract): when a k-mer equals its
    own reverse complement (a reverse-complement palindrome) orientation is
    ``+`` deterministically.
    """
    rc = reverse_complement(kmer)
    if kmer <= rc:
        # Lexicographic order doubles as the documented tie-break: equal
        # strings (palindromes) take the forward orientation.
        return CanonicalKmer(canonical=kmer, orientation="+", kmer=kmer)
    return CanonicalKmer(canonical=rc, orientation="-", kmer=kmer)


def parse_fasta_records(raw: str) -> list[tuple[str, str]]:
    """Split a FASTA-like document into ``(header, sequence)`` pairs.

    Header text excludes the leading ``>``. A document without headers is
    returned as a single unnamed record. Whitespace within sequences is
    removed; characters are validated lazily via :func:`parse_sequence`.
    """
    records: list[tuple[str, list[str]]] = []
    header: str | None = None
    lines: list[str] = []
    saw_header = False
    for line in raw.splitlines():
        if line.strip().startswith(">"):
            saw_header = True
            if header is not None or lines:
                records.append((header or "", lines))
            header = line.strip()[1:].strip()
            lines = []
        else:
            lines.append(line.strip())
    records.append((header or "", lines))
    if not saw_header:
        return [("", "".join(lines))]
    return [(h, "".join(ls)) for h, ls in records]


def iter_kmers(seq: str, k: int):
    """Yield ``(offset, CanonicalKmer)`` for each k-mer without an ``N``.

    K-mers containing an unknown base are skipped (rather than hashed), which
    keeps N-rich reads from generating bogus seeds.
    """
    for i in range(len(seq) - k + 1):
        kmer = seq[i : i + k]
        if "N" in kmer:
            continue
        yield i, canonicalize(kmer)
