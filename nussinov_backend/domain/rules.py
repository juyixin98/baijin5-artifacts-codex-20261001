"""Fixed model rules for the Nussinov teaching model.

These constants are intentionally NOT user-configurable: the allowed base
pairs and the minimum loop length (hairpin) are fixed model parameters.
Pseudoknots are explicitly unsupported: only nested/disjoint non-crossing
pairings are valid.
"""
from __future__ import annotations

from ..errors import InvalidParameterError

# Canonical Watson-Crick pairs plus the GU wobble pair (unordered).
ALLOWED_PAIR_TUPLES: tuple[tuple[str, str], ...] = (
    ("G", "C"),
    ("C", "G"),
    ("A", "U"),
    ("U", "A"),
    ("G", "U"),
    ("U", "G"),
)
ALLOWED_PAIRS = frozenset(ALLOWED_PAIR_TUPLES)

# Standard Nussinov minimum hairpin loop length: at least 3 unpaired bases
# between the two paired bases (pair (i, j) requires j - i - 1 >= 3).
MIN_LOOP_LENGTH = 3

# Non-crossing structures only.
PSEUDOKNOTS_SUPPORTED = False

MODEL_WARNINGS: tuple[str, ...] = (
    "Nussinov is a teaching combinatorial model: it maximizes the number of "
    "canonical non-crossing base pairs only; it does NOT compute folding free "
    "energy and does NOT predict real RNA folding reliability.",
    "Pseudoknots are not supported: any crossing pair is rejected.",
    "When several structures share the optimal pair count, the primary one is "
    "selected by a deterministic rule (position i left unpaired first, then "
    "partners scanned left to right); alternatives can be enumerated on "
    "request up to a fixed cap.",
)


def can_pair(base_i: str, base_j: str) -> bool:
    return (base_i, base_j) in ALLOWED_PAIRS


def pair_label(base_i: str, base_j: str) -> str:
    return f"{base_i}-{base_j}"


def validate_min_loop(min_loop_length: int) -> None:
    """Defensive guard for callers that thread the constant through."""
    if not isinstance(min_loop_length, int) or isinstance(min_loop_length, bool):
        raise InvalidParameterError(
            f"min_loop_length must be an integer, got {type(min_loop_length).__name__}",
            parameter="min_loop_length",
            value=min_loop_length,
        )
    if not 0 <= min_loop_length <= 100:
        raise InvalidParameterError(
            "min_loop_length must be within [0, 100]",
            parameter="min_loop_length",
            value=min_loop_length,
        )
