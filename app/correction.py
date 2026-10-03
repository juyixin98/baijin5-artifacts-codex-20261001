"""Multiple-testing correction across all scored windows of a scan.

Every scored window on either strand is one test of the same null
hypothesis ("this window's score arose from the declared background
model"), so the correction family size is the number of scored windows.
Skipped windows (unknown bases under the skip policy) are excluded — no
p-value exists for them.
"""
from __future__ import annotations


def bonferroni(pvalues: list[float]) -> list[float]:
    n = len(pvalues)
    return [min(1.0, p * n) for p in pvalues]


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    """BH adjusted p-values, returned in the original input order."""
    n = len(pvalues)
    order = sorted(range(n), key=lambda i: pvalues[i])
    adjusted = [0.0] * n
    cumulative_min = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        value = min(cumulative_min, pvalues[i] * n / rank)
        cumulative_min = value
        adjusted[i] = min(1.0, value)
    return adjusted
