"""Exon index built on NumPy for O(log n) coordinate lookups.

For each transcript we precompute, in TRANSCRIPT order (5'->3'):
- which genomic exon (by ascending-genomic index) occupies each transcript slot
- the cumulative transcript offset at the start of each exon

Genomic ascending arrays are kept for binary-searching a genomic position
into its containing exon.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import Strand, Transcript


@dataclass(frozen=True)
class ExonIndex:
    """Precomputed per-transcript lookup tables (immutable)."""

    tx_id: str
    strand: Strand
    # Genomic ascending order:
    g_starts: np.ndarray  # shape (n,), exon start per exon
    g_ends: np.ndarray  # shape (n,), exon end per exon
    # Transcript order (5'->3'): tx_exon_genomic_idx[k] is the index into
    # g_starts/g_ends of the k-th exon in transcript order.
    tx_exon_genomic_idx: np.ndarray  # shape (n,)
    # tx_cum[k] = transcript offset of the first base of the k-th exon
    # (in transcript order).
    tx_cum: np.ndarray  # shape (n,)
    tx_length: int

    @classmethod
    def build(cls, tx: Transcript) -> "ExonIndex":
        g_starts = np.array([e.start for e in tx.exons], dtype=np.int64)
        g_ends = np.array([e.end for e in tx.exons], dtype=np.int64)
        lengths = g_ends - g_starts
        n = len(tx.exons)
        if tx.strand is Strand.PLUS:
            order = np.arange(n, dtype=np.int64)
        else:
            # Minus strand: transcript 5' end is the genomically highest exon.
            order = np.arange(n - 1, -1, -1, dtype=np.int64)
        tx_cum = np.zeros(n, dtype=np.int64)
        tx_cum[1:] = np.cumsum(lengths[order])[:-1]
        return cls(
            tx_id=tx.tx_id,
            strand=tx.strand,
            g_starts=g_starts,
            g_ends=g_ends,
            tx_exon_genomic_idx=order,
            tx_cum=tx_cum,
            tx_length=int(lengths.sum()),
        )

    def find_exon_genomic(self, gpos: int) -> int:
        """Index (genomic ascending) of the exon containing gpos, or -1."""
        # First exon with end > gpos; containment iff start <= gpos.
        i = int(np.searchsorted(self.g_ends, gpos, side="right"))
        if i < len(self.g_ends) and self.g_starts[i] <= gpos < self.g_ends[i]:
            return i
        return -1

    def find_exon_tx(self, tpos: int) -> int:
        """Index (transcript order) of the exon containing tpos, or -1."""
        if tpos < 0 or tpos >= self.tx_length:
            return -1
        # Last cumulative offset <= tpos.
        k = int(np.searchsorted(self.tx_cum, tpos, side="right")) - 1
        return k
