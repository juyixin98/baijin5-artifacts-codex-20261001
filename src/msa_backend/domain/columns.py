"""Per-column weighted residue distributions.

Gap policy (fixed): gaps never enter the distribution. Each column is
normalised by its own non-gap weight, independently of the global weight
total -- so a column with heavy gaps is described by the residues actually
observed, and its low coverage is reported separately via
``effective_coverage`` / ``gap_fraction``.

Ambiguity policy (fixed, ``uniform_split``): an IUPAC degenerate symbol
spreads its sequence's weight uniformly over the concrete bases it
represents. N contributes 1/4 of its weight to each of A/C/G/T; R
contributes half to A and half to G; and so on.
"""

from __future__ import annotations

import numpy as np

from ..parsing.fasta import IUPAC_DNA, Alignment


def column_weighted_counts(
    alignment: Alignment,
    weights: np.ndarray,
    alphabet: tuple[str, ...],
    gap_symbol: str,
    column_index: int,
) -> tuple[np.ndarray, float, float]:
    """Weighted base counts for one column.

    Returns (counts aligned to ``alphabet``, non-gap weight, gap weight).
    """
    counts = np.zeros(len(alphabet), dtype=np.float64)
    base_index = {b: i for i, b in enumerate(alphabet)}
    non_gap_weight = 0.0
    gap_weight = 0.0
    for row, weight in zip(alignment.rows, weights):
        char = row[column_index]
        if char == gap_symbol:
            gap_weight += float(weight)
            continue
        non_gap_weight += float(weight)
        bases = IUPAC_DNA[char]
        share = float(weight) / len(bases)  # uniform_split policy
        for base in bases:
            counts[base_index[base]] += share
    return counts, non_gap_weight, gap_weight


def column_distribution(
    alignment: Alignment,
    weights: np.ndarray,
    alphabet: tuple[str, ...],
    gap_symbol: str,
    column_index: int,
) -> tuple[dict[str, float], float, float]:
    """Weighted frequency distribution for one column.

    Returns (distribution, effective_coverage, gap_fraction). A fully
    gapped column yields a zero distribution and zero coverage; the caller
    (consensus layer) is responsible for not drawing conclusions from it.
    """
    counts, non_gap_weight, gap_weight = column_weighted_counts(
        alignment, weights, alphabet, gap_symbol, column_index
    )
    total_weight = float(weights.sum())
    if non_gap_weight <= 0.0:
        distribution = {b: 0.0 for b in alphabet}
    else:
        distribution = {
            b: float(counts[i] / non_gap_weight) for i, b in enumerate(alphabet)
        }
    gap_fraction = gap_weight / total_weight if total_weight > 0 else 0.0
    return distribution, float(non_gap_weight), gap_fraction
