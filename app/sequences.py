"""Parsing and validation of synthetic aligned sequences.

Input contract
--------------
- A dataset is 2..MAX_SEQUENCES named sequences of equal length
  (they are assumed already aligned; no alignment is performed).
- Alphabet: A/C/G/T are the only *valid comparable* bases.
  Everything else ('-', 'N', IUPAC ambiguity codes such as R/Y/S/W/K/M/B/D/H/V,
  and any other character) is treated as missing/ambiguous data and is
  excluded from comparison site-by-site (pairwise deletion). Such
  characters are legal in the input; they are never an error by themselves.
- Comparison is case-insensitive; sequences are uppercased at parse time.

Errors: malformed input raises InputValidationError; inputs beyond the
configured limits raise ResourceExhaustedError.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InputValidationError, ResourceExhaustedError

VALID_BASES = frozenset("ACGT")

# Resource limits (contract: resource exhaustion must be distinguishable).
MAX_SEQUENCE_LENGTH = 1_000_000
MAX_SEQUENCES = 64
MIN_SEQUENCES = 2


@dataclass(frozen=True)
class AlignedDataset:
    """Validated, normalized set of aligned sequences."""

    ids: tuple[str, ...]
    sequences: tuple[str, ...]  # uppercased, equal length
    length: int


def is_valid_base(char: str) -> bool:
    return char in VALID_BASES


def validate_alignment(ids: list[str], sequences: list[str]) -> AlignedDataset:
    """Normalize and validate a list of (id, sequence) pairs."""
    if not (MIN_SEQUENCES <= len(sequences) <= MAX_SEQUENCES):
        raise InputValidationError(
            "bad_sequence_count",
            f"need {MIN_SEQUENCES}..{MAX_SEQUENCES} sequences, got {len(sequences)}",
        )
    if len(ids) != len(sequences):
        raise InputValidationError("id_sequence_mismatch", "ids and sequences differ in length")

    norm_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in ids:
        sid = raw_id.strip()
        if not sid:
            raise InputValidationError("empty_sequence_id", "sequence id must be non-empty")
        if sid in seen:
            raise InputValidationError(
                "duplicate_sequence_id", f"duplicate sequence id: {sid!r}", {"id": sid}
            )
        seen.add(sid)
        norm_ids.append(sid)

    norm_seqs: list[str] = []
    lengths: set[int] = set()
    for raw_seq in sequences:
        seq = "".join(raw_seq.split()).upper()
        if not seq:
            raise InputValidationError("empty_sequence", "sequence must be non-empty")
        if len(seq) > MAX_SEQUENCE_LENGTH:
            raise ResourceExhaustedError(
                "sequence_too_long",
                f"sequence length {len(seq)} exceeds limit {MAX_SEQUENCE_LENGTH}",
                {"length": len(seq), "limit": MAX_SEQUENCE_LENGTH},
            )
        lengths.add(len(seq))
        norm_seqs.append(seq)

    if len(lengths) != 1:
        raise InputValidationError(
            "unequal_sequence_lengths",
            "aligned sequences must all have the same length",
            {"lengths": sorted(lengths)},
        )

    return AlignedDataset(ids=tuple(norm_ids), sequences=tuple(norm_seqs), length=lengths.pop())


def parse_fasta(text: str) -> AlignedDataset:
    """Parse (multi-line) FASTA text into a validated AlignedDataset."""
    ids: list[str] = []
    seqs: list[str] = []
    current: list[str] | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            ids.append(line[1:].strip())
            current = []
            seqs.append(current)  # type: ignore[arg-type]
        elif current is None:
            raise InputValidationError(
                "fasta_missing_header",
                f"sequence data before any '>' header (line {lineno})",
                {"line": lineno},
            )
        else:
            current.append(line)
    if not ids:
        raise InputValidationError("fasta_empty", "no FASTA records found")
    return validate_alignment(ids, ["".join(parts) for parts in seqs])
