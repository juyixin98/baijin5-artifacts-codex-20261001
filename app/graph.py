"""Skeleton graph extraction.

Node pixels are skeleton pixels whose 8-neighbour degree is not 2:
degree 0 -> isolated, degree 1 -> endpoint, degree >= 3 -> junction.
Adjacent junction pixels (degree >= 3) are clustered (8-connectivity)
into a single node; endpoints and isolated pixels are singleton nodes,
so an endpoint touching a junction cluster is never swallowed by it.

Edges are the degree-2 chains between nodes. Every edge stores the full
ordered pixel chain of the original skeleton, so no geometric information
is lost in the graph mapping. Skeleton cycles with no node on them (e.g.
a perfect ring) become cyclic edges attached to no node.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

_FG_STRUCTURE = np.ones((3, 3), dtype=int)
_NEIGHBOURS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]

Coord = tuple[int, int]


@dataclass(frozen=True)
class GraphNode:
    id: int
    kind: str  # "endpoint" | "junction" | "isolated"
    pixels: tuple[Coord, ...]


@dataclass(frozen=True)
class GraphEdge:
    id: int
    node_ids: tuple[int | None, int | None]
    pixels: tuple[Coord, ...]  # full ordered chain, node pixels included
    cyclic: bool = False


@dataclass(frozen=True)
class SkeletonGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    @property
    def endpoints(self) -> tuple[GraphNode, ...]:
        return tuple(n for n in self.nodes if n.kind == "endpoint")

    @property
    def junctions(self) -> tuple[GraphNode, ...]:
        return tuple(n for n in self.nodes if n.kind == "junction")

    @property
    def cycles(self) -> tuple[GraphEdge, ...]:
        return tuple(e for e in self.edges if e.cyclic)

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": n.id, "kind": n.kind, "pixels": [list(p) for p in n.pixels]}
                for n in self.nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "node_ids": list(e.node_ids),
                    "pixels": [list(p) for p in e.pixels],
                    "cyclic": e.cyclic,
                }
                for e in self.edges
            ],
            "summary": {
                "node_count": len(self.nodes),
                "endpoint_count": len(self.endpoints),
                "junction_count": len(self.junctions),
                "edge_count": len(self.edges),
                "cycle_count": len(self.cycles),
            },
        }


def _degrees(skeleton: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), dtype=int)
    kernel[1, 1] = 0
    return ndimage.convolve(skeleton.astype(int), kernel, mode="constant", cval=0)


def _neighbours(skel: np.ndarray, p: Coord) -> list[Coord]:
    rows, cols = skel.shape
    out = []
    for dr, dc in _NEIGHBOURS:
        r, c = p[0] + dr, p[1] + dc
        if 0 <= r < rows and 0 <= c < cols and skel[r, c]:
            out.append((r, c))
    return out


def skeleton_graph(skeleton: np.ndarray) -> SkeletonGraph:
    """Map a binary skeleton to nodes and pixel-chain edges."""
    skel = np.ascontiguousarray(skeleton).astype(bool)
    deg = _degrees(skel)
    junction_mask = skel & (deg >= 3)
    singleton_mask = skel & (deg <= 1)  # endpoints and isolated pixels
    node_mask = junction_mask | singleton_mask
    labels, count = ndimage.label(junction_mask, structure=_FG_STRUCTURE)

    nodes: list[GraphNode] = []
    pixel_to_node = np.zeros(skel.shape, dtype=int)  # valid where node_mask
    for label_id in range(1, count + 1):
        coords = tuple((int(r), int(c)) for r, c in np.argwhere(labels == label_id))
        nodes.append(GraphNode(id=len(nodes), kind="junction", pixels=coords))
        for p in coords:
            pixel_to_node[p] = len(nodes)  # 1-based during construction
    for r, c in np.argwhere(singleton_mask):
        coord = (int(r), int(c))
        kind = "isolated" if deg[coord] == 0 else "endpoint"
        nodes.append(GraphNode(id=len(nodes), kind=kind, pixels=(coord,)))
        pixel_to_node[coord] = len(nodes)
    pixel_to_node = pixel_to_node - 1  # back to 0-based node ids

    edges: list[GraphEdge] = []
    visited = np.zeros(skel.shape, dtype=bool)  # degree-2 pixels already chained
    direct_seen: set[tuple[Coord, Coord]] = set()

    def add_edge(a: int | None, b: int | None, chain: list[Coord], cyclic: bool) -> None:
        edges.append(
            GraphEdge(id=len(edges), node_ids=(a, b),
                      pixels=tuple(chain), cyclic=cyclic)
        )

    # Edges starting at node pixels.
    for r, c in map(tuple, np.argwhere(node_mask)):
        start = (int(r), int(c))
        start_node = int(pixel_to_node[start])
        for nxt in _neighbours(skel, start):
            if node_mask[nxt]:
                other = int(pixel_to_node[nxt])
                if other == start_node:
                    continue
                key = (min(start, nxt), max(start, nxt))
                if key in direct_seen:
                    continue
                direct_seen.add(key)
                add_edge(start_node, other, [start, nxt], cyclic=False)
                continue
            if visited[nxt]:
                continue
            chain = [start]
            prev, cur = start, nxt
            while True:
                chain.append(cur)
                visited[cur] = True
                candidates = [q for q in _neighbours(skel, cur) if q != prev]
                if len(candidates) != 1:
                    raise RuntimeError(
                        f"degree-2 pixel {cur} has {len(candidates)} forward candidates"
                    )
                following = candidates[0]
                if node_mask[following]:
                    chain.append(following)
                    add_edge(start_node, int(pixel_to_node[following]), chain, cyclic=False)
                    break
                prev, cur = cur, following

    # Remaining unvisited degree-2 pixels form node-less cycles.
    for r, c in map(tuple, np.argwhere(skel & (deg == 2) & ~visited)):
        start = (int(r), int(c))
        if visited[start]:
            continue
        chain = [start]
        visited[start] = True
        prev, cur = start, _neighbours(skel, start)[0]
        while cur != start:
            chain.append(cur)
            visited[cur] = True
            candidates = [q for q in _neighbours(skel, cur) if q != prev]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"cycle trace broken at {cur}: {len(candidates)} candidates"
                )
            prev, cur = cur, candidates[0]
        add_edge(None, None, chain, cyclic=True)

    return SkeletonGraph(nodes=tuple(nodes), edges=tuple(edges))
