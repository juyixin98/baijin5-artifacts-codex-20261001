"""Alignment-column -> original-sequence coordinate mapping.

Insertion columns (residues present in one sequence but aligned against
gaps elsewhere) still correspond to real positions in that sequence's
original, ungapped coordinates. For every sequence we emit, per alignment
column, the 1-based original position or ``None`` for gap cells, so no
inserted residue loses its provenance.
"""

from __future__ import annotations

from ..parsing.fasta import Alignment


def build_coordinate_maps(
    alignment: Alignment, gap_symbol: str
) -> dict[str, tuple[int | None, ...]]:
    maps: dict[str, tuple[int | None, ...]] = {}
    for seq_id, row in zip(alignment.sequence_ids, alignment.rows):
        mapping: list[int | None] = []
        original_position = 0
        for char in row:
            if char == gap_symbol:
                mapping.append(None)
            else:
                original_position += 1
                mapping.append(original_position)
        maps[seq_id] = tuple(mapping)
    return maps
