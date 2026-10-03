"""Fragment x site support matrix for one connected block.

Rows are fragments, columns are the block's variant sites (local order).
``alleles`` holds 0/1 bits (meaningless where ``covered`` is False);
``weights`` holds the phred correction cost of each observation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.domain import Fragment


@dataclass
class FragmentMatrix:
    read_ids: list[str]
    site_indices: list[int]  # global variant indices, ascending
    alleles: np.ndarray    # (F, n) int8
    weights: np.ndarray    # (F, n) float64
    covered: np.ndarray    # (F, n) bool

    @property
    def num_fragments(self) -> int:
        return len(self.read_ids)


def build_matrix(fragments: list[Fragment], site_indices: list[int]) -> FragmentMatrix:
    """Project fragments onto a block's sites, dropping empty rows."""
    column_of = {site: col for col, site in enumerate(site_indices)}
    rows: list[tuple[str, dict[int, tuple[int, float]]]] = []
    for fragment in fragments:
        obs = {
            o.site: (o.bit, o.weight)
            for o in fragment.observations
            if o.site in column_of
        }
        if obs:
            rows.append((fragment.read_id, obs))

    num_rows, num_cols = len(rows), len(site_indices)
    alleles = np.zeros((num_rows, num_cols), dtype=np.int8)
    weights = np.zeros((num_rows, num_cols), dtype=np.float64)
    covered = np.zeros((num_rows, num_cols), dtype=bool)
    for row, (read_id, obs) in enumerate(rows):
        for site, (bit, weight) in obs.items():
            col = column_of[site]
            alleles[row, col] = bit
            weights[row, col] = weight
            covered[row, col] = True
    return FragmentMatrix(
        read_ids=[read_id for read_id, _ in rows],
        site_indices=list(site_indices),
        alleles=alleles,
        weights=weights,
        covered=covered,
    )
