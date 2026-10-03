"""Exact Minimum Error Correction (MEC) solver for one block.

Objective: choose a diploid haplotype pair (h1, h2) with h2 = 1 - h1
(biallelic sites) minimising

    MEC(h1) = sum_over_fragments min( d(f, h1), d(f, h2) )

where d(f, h) sums the phred weights of observations contradicting h.
Conflicting fragment support is therefore resolved by the minimum total
error-correction cost, exactly as the weighted-MEC formulation requires.

Phase-flip equivalence: (h1, h2) and (1-h1, 1-h2) are the same biological
solution, so enumeration is restricted to canonical representatives with
h1[0] == 0, halving the search space and giving deterministic output.

All optima are reported: a tie means the data cannot distinguish the
solutions, which is surfaced as ambiguity rather than an arbitrary pick.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.phasing.matrix import FragmentMatrix


@dataclass
class MECResult:
    best_score: float
    # All score-optimal canonical haplotypes (h1[0] == 0), ascending score order
    # is guaranteed since they all share best_score; order is by integer code.
    optimal_haplotypes: list[np.ndarray]
    # Per-fragment assignment for the first optimum: 0 -> h1, 1 -> h2.
    assignments: np.ndarray
    # Per-fragment correction cost under the first optimum.
    correction_costs: np.ndarray
    # Fragments (row indices) equally close to h1 and h2 under the first optimum.
    tied_fragments: list[int]


def _canonical_candidates(num_sites: int) -> np.ndarray:
    """All 2**(n-1) haplotypes with bit 0 fixed to 0, shape (2**(n-1), n)."""
    codes = np.arange(2 ** (num_sites - 1), dtype=np.uint64)
    candidates = np.zeros((len(codes), num_sites), dtype=np.int8)
    for site in range(1, num_sites):
        candidates[:, site] = ((codes >> np.uint64(site - 1)) & np.uint64(1)).astype(np.int8)
    return candidates


def solve_mec(matrix: FragmentMatrix) -> MECResult:
    """Solve weighted MEC exactly by enumerating canonical haplotypes."""
    num_sites = len(matrix.site_indices)
    weighted = matrix.weights * matrix.covered            # WM[f, s]
    observed = weighted * matrix.alleles                  # WM[f, s] where bit==1
    candidates = _canonical_candidates(num_sites)         # (C, n)

    # d(f, h) = sum_s WM[f,s] * (A[f,s] != h[s])
    #         = P[f] + (WM @ h)[f] - 2 * (observed @ h)[f],  P[f] = sum_s observed[f,s]
    # Vectorised over all candidates at once.
    p = observed.sum(axis=1)                              # (F,)
    d1 = p[:, None] + weighted @ candidates.T - 2.0 * (observed @ candidates.T)
    total_weight = weighted.sum(axis=1)                   # (F,)
    d2 = total_weight[:, None] - d1                       # distance to complement h2

    mec = np.minimum(d1, d2).sum(axis=0)                  # (C,)
    best = float(mec.min())
    optimal_codes = np.flatnonzero(mec == best)

    first = int(optimal_codes[0])
    assign_h2 = d2[:, first] < d1[:, first]
    assignments = assign_h2.astype(np.int8)
    correction_costs = np.minimum(d1[:, first], d2[:, first])
    covers_any = matrix.covered.any(axis=1)
    tied = np.flatnonzero((d1[:, first] == d2[:, first]) & covers_any).tolist()

    return MECResult(
        best_score=best,
        optimal_haplotypes=[candidates[int(code)] for code in optimal_codes],
        assignments=assignments,
        correction_costs=correction_costs,
        tied_fragments=tied,
    )
