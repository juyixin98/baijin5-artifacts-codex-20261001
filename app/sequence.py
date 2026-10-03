"""Synthetic DNA sequence parsing.

Accepts either a raw sequence string or (multi-record) FASTA text and
produces normalised :class:`SequenceRecord` objects.

Declared rules for non-ACGT content
-----------------------------------
* IUPAC ambiguity letters (N, R, Y, ...) are *kept* as unknown bases ('N');
  how they are scored is a scanning concern (skip / marginalize policy).
* Any non-letter character (digits, punctuation, ...) is a hard error:
  the input is not a synthetic DNA sequence at all.
* Empty records or empty input are hard errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.errors import DomainError, ErrorCategory

#: IUPAC ambiguity codes accepted and normalised to 'N' (unknown base).
_AMBIGUOUS_LETTERS = set("NRYSWKMBDHV")


@dataclass(frozen=True)
class SequenceRecord:
    seq_id: str
    # Upper-case sequence over {A, C, G, T, N}; 'N' marks any unknown base.
    bases: str

    @property
    def length(self) -> int:
        return len(self.bases)


def _normalise_bases(raw: str, seq_id: str) -> str:
    out: list[str] = []
    for pos, ch in enumerate(raw):
        if not ch.isalpha():
            raise DomainError(
                ErrorCategory.INVALID_SEQUENCE,
                f"sequence '{seq_id}' contains non-letter character {ch!r} at offset {pos}",
                {"seq_id": seq_id, "offset": pos, "character": ch},
            )
        up = ch.upper()
        if up in "ACGT":
            out.append(up)
        elif up in _AMBIGUOUS_LETTERS:
            out.append("N")
        else:
            # Letters outside IUPAC (e.g. 'U', 'X', 'J') are not DNA.
            raise DomainError(
                ErrorCategory.INVALID_SEQUENCE,
                f"sequence '{seq_id}' contains non-DNA letter {ch!r} at offset {pos}",
                {"seq_id": seq_id, "offset": pos, "character": ch},
            )
    if not out:
        raise DomainError(
            ErrorCategory.INVALID_SEQUENCE,
            f"sequence '{seq_id}' is empty",
            {"seq_id": seq_id},
        )
    return "".join(out)


def parse_sequences(text: str) -> list[SequenceRecord]:
    """Parse raw or FASTA input into normalised sequence records."""
    if not text or not text.strip():
        raise DomainError(ErrorCategory.INVALID_SEQUENCE, "no sequence data provided")

    records: list[SequenceRecord] = []
    current_id: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        nonlocal current_id, chunks
        if current_id is None:
            return
        seq_id = current_id or f"seq{len(records) + 1}"
        records.append(SequenceRecord(seq_id=seq_id, bases=_normalise_bases("".join(chunks), seq_id)))
        current_id, chunks = None, []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            current_id = line[1:].strip()  # may be empty -> auto-named at flush
            chunks = []
        else:
            if current_id is None:
                current_id = ""  # raw sequence without header -> auto-named
            chunks.append(line)
    flush()

    if not records:
        raise DomainError(ErrorCategory.INVALID_SEQUENCE, "no sequence records parsed")
    return records
