"""Independent scalar re-computation of the Nussinov optimum.

This is a deliberately simple, plain-Python implementation of the same
recurrence (nested loops, no NumPy, different code path). It exists so the
production service can self-check that the vectorized fill and every
traceback agree on the real optimum. It is NOT the test oracle: tests ship
their own exhaustive enumerator under ``tests/reference``.
"""
from __future__ import annotations

from .rules import MIN_LOOP_LENGTH, can_pair


def scalar_optimum(sequence: str, min_loop_length: int = MIN_LOOP_LENGTH) -> int:
    n = len(sequence)
    dp = [[0] * n for _ in range(n)]
    for d in range(1, n):
        for i in range(n - d):
            j = i + d
            best = dp[i + 1][j]  # i unpaired
            for k in range(i + min_loop_length + 1, j + 1):
                if can_pair(sequence[i], sequence[k]):
                    inner = dp[i + 1][k - 1] if i + 1 <= k - 1 else 0
                    outer = dp[k + 1][j] if k + 1 <= j else 0
                    best = max(best, 1 + inner + outer)
            dp[i][j] = best
    return 0 if n == 0 else dp[0][n - 1]


def verify_table(sequence: str, optimum: int, min_loop_length: int = MIN_LOOP_LENGTH) -> None:
    """Raise AssertionError if the independent scalar optimum disagrees."""
    expected = scalar_optimum(sequence, min_loop_length)
    if expected != optimum:
        raise AssertionError(
            f"vectorized optimum {optimum} != independent scalar optimum {expected}"
        )
