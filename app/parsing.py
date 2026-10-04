"""Parsing of synthetic sequence/matrix inputs.

Boundary contract:
- Input: raw text (FASTA) or already-structured matrix payloads.
- Output: ``(labels, square_matrix_as_lists_of_float)``.
- Errors: only :class:`InputValidationError`. Nothing here silently repairs
  input; anything suspicious is rejected with a specific message.

The sequence alphabet is deliberately strict (A/C/G/T only, case-insensitive)
because the fixtures are synthetic; ambiguous bases are an input error, not
something to guess at. Distances are p-distances (mismatch fraction).
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InputValidationError

VALID_BASES = frozenset("ACGT")
MAX_FASTA_CHARS = 10_000_000


@dataclass(frozen=True)
class SequenceRecord:
    label: str
    sequence: str


def parse_fasta(text: str) -> list[SequenceRecord]:
    """Parse FASTA text into records, rejecting malformed input."""
    if not isinstance(text, str) or not text.strip():
        raise InputValidationError("FASTA input is empty")
    if len(text) > MAX_FASTA_CHARS:
        raise InputValidationError(
            "FASTA input exceeds size limit",
            {"limit_chars": MAX_FASTA_CHARS, "got_chars": len(text)},
        )

    records: list[SequenceRecord] = []
    label: str | None = None
    chunks: list[str] = []
    seen_labels: set[str] = set()

    def flush() -> None:
        nonlocal label, chunks
        if label is None:
            return
        seq = "".join(chunks).upper()
        if not seq:
            raise InputValidationError(
                "sequence has no residues", {"label": label}
            )
        bad = sorted(set(seq) - VALID_BASES)
        if bad:
            raise InputValidationError(
                "sequence contains characters outside the A/C/G/T alphabet",
                {"label": label, "invalid_characters": bad},
            )
        records.append(SequenceRecord(label=label, sequence=seq))
        label, chunks = None, []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            label = line[1:].strip()
            if not label:
                raise InputValidationError(
                    "FASTA header without a label", {"line": lineno}
                )
            if label in seen_labels:
                raise InputValidationError(
                    "duplicate sequence label",
                    {"label": label, "line": lineno},
                )
            seen_labels.add(label)
        else:
            if label is None:
                raise InputValidationError(
                    "sequence data before the first FASTA header",
                    {"line": lineno},
                )
            chunks.append(line)
    flush()

    if not records:
        raise InputValidationError("FASTA input contained no sequences")
    return records


def p_distance_matrix(
    records: list[SequenceRecord],
) -> tuple[list[str], list[list[float]]]:
    """Compute the p-distance (mismatch fraction) matrix.

    All sequences must have equal length; there are no gaps in the synthetic
    alphabet, so any length mismatch is an input error.
    """
    if len(records) < 2:
        raise InputValidationError(
            "at least 2 sequences are required", {"got": len(records)}
        )
    lengths = {len(r.sequence) for r in records}
    if len(lengths) != 1:
        raise InputValidationError(
            "sequences have unequal lengths",
            {"lengths": {r.label: len(r.sequence) for r in records}},
        )
    length = lengths.pop()
    labels = [r.label for r in records]
    seqs = [r.sequence for r in records]
    n = len(seqs)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        si = seqs[i]
        for j in range(i + 1, n):
            mismatches = sum(1 for a, b in zip(si, seqs[j]) if a != b)
            d = mismatches / length
            matrix[i][j] = d
            matrix[j][i] = d
    return labels, matrix
