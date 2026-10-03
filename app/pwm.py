"""Position weight matrix model and log-odds scoring.

Score of a k-mer = sum over positions of log2(PWM_prob / background_prob).
The PWM probabilities come from a count matrix plus an explicit pseudocount;
the background comes from a validated BackgroundModel. Both ingredients are
declared inputs, never hidden constants.
"""
from __future__ import annotations

import math

import numpy as np

from .config import BASES, UNKNOWN_BASE, BackgroundModel
from .errors import (
    InvalidCharacterError,
    InvalidMotifMatrixError,
    InvalidPseudocountError,
    UnknownBasePolicyError,
)

_BASE_INDEX = {b: i for i, b in enumerate(BASES)}


class PWM:
    """A position weight matrix expressed as log2 likelihood ratios."""

    def __init__(
        self,
        matrix: list[list[float]],
        background: BackgroundModel,
        pseudocount: float,
        name: str = "motif",
    ):
        self.name = name
        self.background = background
        self.pseudocount = pseudocount

        if pseudocount <= 0 or not math.isfinite(pseudocount):
            raise InvalidPseudocountError(
                f"pseudocount must be a positive finite number, got {pseudocount!r}",
                {"pseudocount": pseudocount},
            )
        if not matrix or not isinstance(matrix, list):
            raise InvalidMotifMatrixError("motif matrix must be a non-empty list of rows", {})
        for i, row in enumerate(matrix):
            if not isinstance(row, list) or len(row) != len(BASES):
                raise InvalidMotifMatrixError(
                    f"motif matrix row {i} must have exactly {len(BASES)} "
                    "entries (A, C, G, T order)",
                    {"row": i},
                )
            if any((not isinstance(v, (int, float))) or not math.isfinite(v) or v < 0 for v in row):
                raise InvalidMotifMatrixError(
                    f"motif matrix row {i} contains a negative or non-finite count",
                    {"row": i},
                )

        counts = np.asarray(matrix, dtype=np.float64)
        smoothed = counts + pseudocount
        self.probs = smoothed / smoothed.sum(axis=1, keepdims=True)
        bg = np.array([background.probs[b] for b in BASES], dtype=np.float64)
        # background probs are guaranteed > 0 by BackgroundModel validation
        self.log_odds = np.log2(self.probs / bg[None, :])

    @property
    def length(self) -> int:
        return int(self.probs.shape[0])

    def score(self, kmer: str, marginalize_unknown: bool = False) -> float:
        """Log2 likelihood-ratio score of one k-mer.

        Unknown base (N) handling: with ``marginalize_unknown=True`` the
        position contributes log2(sum_b bg(b) * PWM(b)/bg(b)) = log2(1) = 0,
        i.e. the likelihood ratio marginalized over the background. Without
        it, scoring a window containing N is a declared policy error — the
        caller (scanner) must have applied the skip policy already.
        """
        if len(kmer) != self.length:
            raise InvalidCharacterError(
                f"k-mer length {len(kmer)} does not match motif length {self.length}",
                {"kmer": kmer},
            )
        total = 0.0
        for pos, ch in enumerate(kmer):
            if ch in _BASE_INDEX:
                total += float(self.log_odds[pos, _BASE_INDEX[ch]])
            elif ch == UNKNOWN_BASE:
                if marginalize_unknown:
                    continue  # contributes exactly 0.0, see docstring
                raise UnknownBasePolicyError(
                    "window contains unknown base 'N' but no handling policy "
                    "was applied; declare 'skip' or 'marginalize'",
                    {"position": pos},
                )
            else:
                raise InvalidCharacterError(
                    f"invalid character {ch!r} in k-mer",
                    {"position": pos, "character": ch},
                )
        return total
