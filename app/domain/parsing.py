"""Synthetic protein sequence parsing and validation.

Boundary convention (uniform across the whole service):

* Positions are **1-based residue indices** in the *parsed* sequence, i.e.
  residue ``i`` is ``sequence[i-1]``.
* A peptide fragment spanning residues ``[start, end]`` inclusive uses
  Python-style half-open slicing ``sequence[start-1:end]``.
* The N-terminus lies before residue 1; the C-terminus lies after residue
  ``n`` where ``n == len(sequence)``.
* Bonds are identified by the 1-based index of the residue on their
  N-terminal side: bond ``i`` joins residue ``i`` to residue ``i+1`` and is
  located between half-open positions ``i`` and ``i+1``.

Whitespace inside sequences is rejected (``ILLEGAL_SYMBOL``) rather than
silently stripped, so concatenation mistakes are surfaced. Empty / all-blank
input is ``EMPTY_SEQUENCE``. Letters that are valid tokens but absent from the
supported alphabet raise ``UNKNOWN_RESIDUE`` — callers decide whether that is
fatal or yields an unknown-mass state; it is never silently treated as known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.errors import (
    EmptySequenceError,
    IllegalSymbolError,
    SequenceTooLongError,
    UnknownResidueError,
)

# Standard 20 amino acids. Synthetic X (any residue) and B/Z/J ambiguities are
# handled by the mass layer, NOT silently accepted here as known masses.
KNOWN_RESIDUES = frozenset("ACDEFGHIKLMNPQRSTVWY")

# Single uppercase letters that are legal *tokens* but have no fixed mass here.
#   X = unknown/any residue; B = Asp/Asn; Z = Glu/Gln; J = Leu/Ile
AMBIGUOUS_TOKENS = frozenset("BZJ")
UNKNOWN_OR_AMBIGUOUS_TOKENS = frozenset("XBZJ")

# Only ASCII letters are residue tokens: '*' (stop), digits and whitespace are
# illegal symbols and rejected explicitly rather than silently skipped.
LEGAL_TOKEN_RE = re.compile(r"^[A-Za-z]$")


@dataclass(frozen=True)
class ParsedSequence:
    """A normalized sequence plus 1-based positional metadata."""

    sequence: str
    length: int

    @property
    def n_terminus(self) -> int:
        """Half-open position immediately before residue 1."""
        return 0

    @property
    def c_terminus(self) -> int:
        """Half-open position immediately after the final residue."""
        return self.length


def normalize_sequence(raw: str) -> str:
    """Uppercase without deleting anything; whitespace/illegals are reported."""
    if raw is None:
        raise EmptySequenceError("sequence is required", {"reason": "null"})
    text = raw.strip()
    if not text:
        raise EmptySequenceError(
            "sequence must contain at least one residue", {"reason": "blank"}
        )
    # Internal whitespace, digits, punctuation: explicit failure, no silent edit.
    for ch in raw:
        if not LEGAL_TOKEN_RE.match(ch):
            raise IllegalSymbolError(
                f"illegal symbol {ch!r} in sequence; no whitespace or "
                "non-letter tokens allowed",
                {"symbol": ch, "allowed": "A-Z letters"},
            )
    return raw.upper()


def parse_sequence(raw: str, max_length: int) -> ParsedSequence:
    """Validate and wrap a raw sequence string."""
    seq = normalize_sequence(raw)
    if len(seq) > max_length:
        raise SequenceTooLongError(
            f"sequence length {len(seq)} exceeds configured maximum {max_length}",
            {"length": len(seq), "max_length": max_length},
        )
    return ParsedSequence(sequence=seq, length=len(seq))


def unknown_positions(seq: str) -> list[int]:
    """Return 1-based positions of X tokens (mass unknowable)."""
    return [i for i, ch in enumerate(seq, start=1) if ch == "X"]


def ambiguous_positions(seq: str) -> list[int]:
    """Return 1-based positions of bounded-ambiguity tokens B/Z/J."""
    return [i for i, ch in enumerate(seq, start=1) if ch in AMBIGUOUS_TOKENS]


def unsupported_letter_positions(seq: str) -> list[int]:
    """Letters that are legal tokens but neither standard, X, nor B/Z/J.

    Examples: ``U`` (selenocysteine) and ``O`` (pyrrolysine) — supported by
    neither the mass table nor a bounded ambiguity group. They are reported as
    :class:`UnknownResidueError` by strict paths, distinct from illegal
    non-letter symbols.
    """
    return [
        i
        for i, ch in enumerate(seq, start=1)
        if ch not in KNOWN_RESIDUES and ch not in UNKNOWN_OR_AMBIGUOUS_TOKENS
    ]


def ensure_no_unknown_residues(seq: str) -> None:
    """Raise :class:`UnknownResidueError` for X/B/Z/J or unsupported letters.

    Used by strict call paths. The mass layer instead surfaces an *ambiguous*
    (B/Z/J) or *unknown* (X) state for the recognized tokens and also reports
    unsupported letters as unknown; the two behaviors are deliberately
    distinct.
    """
    positions = unknown_positions(seq)
    if positions:
        token = seq[positions[0] - 1]
        raise UnknownResidueError(
            f"residue {token!r} at 1-based position {positions[0]} is an unknown "
            "residue (X) with no candidate mass",
            {
                "token": token,
                "first_position": positions[0],
                "positions": positions,
                "supported": "".join(sorted(KNOWN_RESIDUES)),
            },
        )
    unsupported = unsupported_letter_positions(seq)
    if unsupported:
        token = seq[unsupported[0] - 1]
        raise UnknownResidueError(
            f"residue {token!r} at 1-based position {unsupported[0]} is not in "
            "the supported fixed-mass alphabet (ACDEFGHIKLMNPQRSTVWY)",
            {
                "token": token,
                "first_position": unsupported[0],
                "positions": unsupported,
                "supported": "".join(sorted(KNOWN_RESIDUES)),
            },
        )
