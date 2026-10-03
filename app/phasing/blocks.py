"""Connected components of the fragment-overlap graph.

Two variant sites belong to the same phasing block iff some fragment
reports a usable allele at both (transitively). Blocks are phased
independently; no phase relation is ever fabricated across blocks.
"""

from __future__ import annotations

from app.domain import Fragment


def find_blocks(num_sites: int, fragments: list[Fragment]) -> list[list[int]]:
    """Return blocks as sorted lists of global site indices."""
    parent = list(range(num_sites))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for fragment in fragments:
        sites = fragment.sites
        for other in sites[1:]:
            union(sites[0], other)

    groups: dict[int, list[int]] = {}
    for site in range(num_sites):
        groups.setdefault(find(site), []).append(site)
    return sorted((sorted(members) for members in groups.values()), key=lambda m: m[0])
