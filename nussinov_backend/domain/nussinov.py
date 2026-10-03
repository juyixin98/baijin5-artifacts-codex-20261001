"""Nussinov dynamic-programming table fill (NumPy).

Recurrence for the maximum number of pairs on s[i..j] inclusive::

    dp[i, j] = max(
        dp[i + 1, j],                                              # i unpaired
        max over k, i paired with k:
            1 + dp[i + 1, k - 1] + dp[k + 1, j]                   # i-k paired
    )

A pair (i, k) is admissible only when the bases are canonically compatible
(see :mod:`rules`) AND ``k - i - 1 >= MIN_LOOP_LENGTH``. The fill is
vectorized along diagonals with NumPy; the scalar, independent re-computation
used for verification lives in :mod:`verification`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rules import ALLOWED_PAIRS, MIN_LOOP_LENGTH, validate_min_loop

_BASE_CODE = {"A": 1, "C": 2, "G": 3, "U": 4}


def _compatibility_matrix(sequence: str) -> np.ndarray:
    n = len(sequence)
    codes = np.array([_BASE_CODE[b] for b in sequence], dtype=np.int8)
    row = codes[:, np.newaxis]
    col = codes[np.newaxis, :]
    can = np.zeros((n, n), dtype=bool)
    for left, right in ALLOWED_PAIRS:
        can |= (row == _BASE_CODE[left]) & (col == _BASE_CODE[right])
    return can


@dataclass(frozen=True)
class NussinovTable:
    sequence: str
    dp: np.ndarray
    can_pair: np.ndarray
    min_loop_length: int

    @property
    def size(self) -> int:
        return len(self.sequence)

    @property
    def optimum(self) -> int:
        if self.size == 0:
            return 0
        return int(self.dp[0, self.size - 1])

    def value(self, i: int, j: int) -> int:
        """dp value with 0 for empty intervals (i > j)."""
        if i > j:
            return 0
        return int(self.dp[i, j])

    def pairable(self, i: int, j: int) -> bool:
        if not (0 <= i < self.size and 0 <= j < self.size):
            return False
        return bool(self.can_pair[i, j])


def fill_dp(sequence: str, min_loop_length: int = MIN_LOOP_LENGTH) -> NussinovTable:
    """Fill the Nussinov DP table for a normalized ACGU sequence."""
    validate_min_loop(min_loop_length)
    n = len(sequence)
    dp = np.zeros((n, n), dtype=np.int32)
    if n == 0:
        return NussinovTable(sequence, dp, np.zeros((0, 0), dtype=bool), min_loop_length)

    can = _compatibility_matrix(sequence)

    # d = j - i; fill by increasing span (d=0 diagonal stays 0).
    for d in range(1, n):
        iv = np.arange(n - d)
        jv = iv + d
        best = dp[iv + 1, jv].copy()  # branch: base i stays unpaired

        for t in range(min_loop_length + 1, d + 1):  # t = k - i
            kv = iv + t
            compatible = can[iv, kv]
            left = dp[iv + 1, kv - 1]
            if t == d:
                right = np.zeros(iv.shape[0], dtype=np.int32)  # empty k+1..j
            else:
                right = dp[kv + 1, jv]
            candidate = (1 + left + right).astype(np.int32)
            best = np.maximum(best, np.where(compatible, candidate, -1))

        dp[iv, jv] = best

    return NussinovTable(sequence, dp, can, min_loop_length)
