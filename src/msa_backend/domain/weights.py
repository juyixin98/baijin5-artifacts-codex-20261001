"""Near-duplicate sequence weighting.

Rationale: a synthetic benchmark (or a skewed sample) may contain many
near-identical copies of one sequence. Counting each copy as independent
evidence would amplify whatever residues the copies carry. We therefore:

1. compute pairwise identity over positions where BOTH sequences are
   non-gap (gap handling here is independent of column-level gap handling:
   it only affects the identity estimate, never the weight total);
2. cluster sequences whose identity >= ``identity_threshold`` (connected
   components of the "similar" graph);
3. give every cluster total weight 1.0, split equally among members.

The total weight therefore equals the number of distinct clusters -- the
effective number of independent sequences -- regardless of copy counts.
"""

from __future__ import annotations

import numpy as np

from ..parsing.fasta import Alignment


def pairwise_identity(row_a: str, row_b: str, gap_symbol: str = "-") -> float:
    """Identity over positions where both rows are non-gap.

    Returns 1.0 when there is nothing to compare (both fully gapped) so an
    all-gap pair is treated as identical-by-absence rather than crashing.
    """
    matches = 0
    compared = 0
    for a, b in zip(row_a, row_b):
        if a == gap_symbol or b == gap_symbol:
            continue
        compared += 1
        if a == b:
            matches += 1
    if compared == 0:
        return 1.0
    return matches / compared


def build_clusters(alignment: Alignment, identity_threshold: float, gap_symbol: str) -> list[list[int]]:
    """Connected components of the pairwise-identity graph (row indices)."""
    n = alignment.n_sequences
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            identity = pairwise_identity(alignment.rows[i], alignment.rows[j], gap_symbol)
            if identity >= identity_threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)
    return [sorted(members) for members in groups.values()]


def compute_weights(
    alignment: Alignment, identity_threshold: float, gap_symbol: str
) -> tuple[dict[str, float], list[list[str]], np.ndarray]:
    """Return (weights by id, clusters by id, weight vector aligned to rows)."""
    clusters_idx = build_clusters(alignment, identity_threshold, gap_symbol)
    weights_vector = np.zeros(alignment.n_sequences, dtype=np.float64)
    weights_by_id: dict[str, float] = {}
    clusters_by_id: list[list[str]] = []
    for members in clusters_idx:
        share = 1.0 / len(members)
        clusters_by_id.append([alignment.sequence_ids[m] for m in members])
        for m in members:
            weights_vector[m] = share
            weights_by_id[alignment.sequence_ids[m]] = share
    return weights_by_id, clusters_by_id, weights_vector
