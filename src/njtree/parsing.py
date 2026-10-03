"""Synthetic sequence parsing boundary.

Contract
--------
``parse_fasta`` turns synthetic FASTA text into ``(label, sequence)`` records.
``hamming_matrix`` turns equal-length records into a ``DistanceMatrix`` of
mismatch counts (Hamming distance, not a corrected distance).

All malformed input raises ``InputValidationError`` with the offending line
or label in ``details`` -- nothing is silently skipped or truncated.
"""

from __future__ import annotations

import numpy as np

from .errors import InputValidationError
from .matrix import LABEL_RE, validate_distance_matrix
from .models import DEFAULT_MAX_TAXA, DistanceMatrix

DEFAULT_ALPHABET = frozenset("ACGT")


def parse_fasta(text: str, *, alphabet: frozenset[str] = DEFAULT_ALPHABET) -> list[tuple[str, str]]:
    """Parse synthetic FASTA. Strict: every character must be in the alphabet,
    every label must match the label charset, sequences must be non-empty."""
    if not isinstance(text, str) or not text.strip():
        raise InputValidationError("fasta input is empty")

    records: list[tuple[str, str]] = []
    label: str | None = None
    chunks: list[str] = []

    def flush(lineno: int) -> None:
        nonlocal label, chunks
        if label is None:
            return
        seq = "".join(chunks)
        if not seq:
            raise InputValidationError(f"empty sequence for label {label!r}",
                                       details={"label": label, "line": lineno})
        records.append((label, seq))

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush(lineno)
            label = line[1:].strip()
            chunks = []
            if not label:
                raise InputValidationError("empty fasta label", details={"line": lineno})
            if not LABEL_RE.match(label):
                raise InputValidationError(
                    f"label {label!r} does not match charset [A-Za-z0-9_.-]",
                    details={"label": label, "line": lineno})
        else:
            if label is None:
                raise InputValidationError("sequence data before first fasta header",
                                           details={"line": lineno})
            seq = line.upper()
            invalid = sorted(set(seq) - set(alphabet))
            if invalid:
                raise InputValidationError(
                    f"invalid characters {invalid} in sequence {label!r}",
                    details={"label": label, "line": lineno, "invalid": invalid})
            chunks.append(seq)
    flush(len(text.splitlines()) + 1)

    if not records:
        raise InputValidationError("fasta input contains no records")
    labels = [r[0] for r in records]
    if len(set(labels)) != len(labels):
        dupes = sorted({x for x in labels if labels.count(x) > 1})
        raise InputValidationError("duplicate fasta labels", details={"duplicates": dupes})
    return records


def hamming_matrix(
    records: list[tuple[str, str]],
    *,
    max_taxa: int = DEFAULT_MAX_TAXA,
) -> DistanceMatrix:
    """Pairwise mismatch counts. Requires equal-length sequences."""
    if not records:
        raise InputValidationError("no sequences provided")
    lengths = {len(seq) for _, seq in records}
    if len(lengths) != 1:
        raise InputValidationError(
            "sequences have unequal lengths",
            details={"lengths": {label: len(seq) for label, seq in records}})

    labels = [label for label, _ in records]
    n = len(records)
    encoded = [np.frombuffer(seq.encode("ascii"), dtype=np.uint8) for _, seq in records]
    values = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            mismatches = float(np.count_nonzero(encoded[i] != encoded[j]))
            values[i, j] = values[j, i] = mismatches
    # Reuse the same validation boundary as raw matrices (labels, finiteness...).
    return validate_distance_matrix(labels, values.tolist(), max_taxa=max_taxa)
