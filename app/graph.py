"""Skeleton graph extraction.

Nodes are endpoints (degree 1), junction clusters (degree >= 3, 8-connected
clusters merged into one node), isolated pixels (degree 0) and cycle anchors
(pure loops with no endpoint/junction). Edges store the ORIGINAL PIXEL CHAIN
(row, col) from one node to the other, so downstream consumers can map every
graph edge back to exact image coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .topology import STRUCTURE_FG, neighbor_count

_OFFSETS_8 = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]


@dataclass
class Node:
    id: int
    kind: str  # endpoint | junction | isolated | cycle_anchor
    pixels: list[tuple[int, int]]

    @property
    def centroid(self) -> tuple[float, float]:
        rs = [p[0] for p in self.pixels]
        cs = [p[1] for p in self.pixels]
        return (sum(rs) / len(rs), sum(cs) / len(cs))


@dataclass
class Edge:
    id: int
    node_a: int
    node_b: int
    pixels: list[tuple[int, int]]  # full chain, inclusive of both node pixels


@dataclass
class SkeletonGraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "pixels": [list(p) for p in n.pixels],
                    "centroid": [round(c, 3) for c in n.centroid],
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "node_a": e.node_a,
                    "node_b": e.node_b,
                    "length": len(e.pixels),
                    "pixels": [list(p) for p in e.pixels],
                }
                for e in self.edges
            ],
        }


def _neighbors8(p: tuple[int, int], shape: tuple[int, int]):
    r, c = p
    for dr, dc in _OFFSETS_8:
        nr, nc = r + dr, c + dc
        if 0 <= nr < shape[0] and 0 <= nc < shape[1]:
            yield (nr, nc)


def extract_graph(skel: np.ndarray) -> SkeletonGraph:
    s = (skel != 0).astype(np.uint8)
    deg = neighbor_count(s)

    graph = SkeletonGraph()
    node_id_of = np.full(s.shape, -1, dtype=int)

    # Endpoints and isolated pixels are always individual nodes; only
    # junction pixels (deg >= 3) are merged into 8-connected clusters.
    junction_labels, _ = ndimage.label((s == 1) & (deg >= 3), structure=STRUCTURE_FG)
    for label_id in range(1, int(junction_labels.max()) + 1 if junction_labels.size else 0):
        pts = [(int(r), int(c)) for r, c in np.argwhere(junction_labels == label_id)]
        if not pts:
            continue
        node = Node(id=len(graph.nodes), kind="junction", pixels=pts)
        graph.nodes.append(node)
        for p in pts:
            node_id_of[p] = node.id
    for p in map(lambda a: (int(a[0]), int(a[1])), np.argwhere((s == 1) & (deg <= 1))):
        kind = "isolated" if deg[p] == 0 else "endpoint"
        node = Node(id=len(graph.nodes), kind=kind, pixels=[p])
        graph.nodes.append(node)
        node_id_of[p] = node.id

    visited_steps: set[frozenset] = set()

    def trace(start: tuple[int, int], first: tuple[int, int]) -> list[tuple[int, int]]:
        chain = [start]
        prev, cur = start, first
        visited_steps.add(frozenset((prev, cur)))
        while node_id_of[cur] == -1:
            chain.append(cur)
            nxt = None
            for q in _neighbors8(cur, s.shape):
                if s[q] and q != prev:
                    nxt = q
                    break
            if nxt is None:
                break
            visited_steps.add(frozenset((cur, nxt)))
            prev, cur = cur, nxt
        chain.append(cur)
        return chain

    # Edges anchored at nodes.
    for node in graph.nodes:
        for p in node.pixels:
            for q in _neighbors8(p, s.shape):
                if not s[q] or node_id_of[q] == node.id:
                    continue
                if frozenset((p, q)) in visited_steps:
                    continue
                chain = trace(p, q)
                other = int(node_id_of[chain[-1]])
                graph.edges.append(
                    Edge(id=len(graph.edges), node_a=node.id, node_b=other, pixels=chain)
                )

    # Pure cycles: skeleton pixels unreachable from any node (all degree 2).
    for p in map(lambda a: (int(a[0]), int(a[1])), np.argwhere((s == 1) & (node_id_of == -1))):
        first = None
        for q in _neighbors8(p, s.shape):
            if s[q] and frozenset((p, q)) not in visited_steps:
                first = q
                break
        if first is None:
            continue
        anchor = Node(id=len(graph.nodes), kind="cycle_anchor", pixels=[p])
        graph.nodes.append(anchor)
        node_id_of[p] = anchor.id
        chain = trace(p, first)
        # trace stops when it returns to a node pixel, i.e. the anchor itself.
        graph.edges.append(
            Edge(id=len(graph.edges), node_a=anchor.id, node_b=anchor.id, pixels=chain)
        )

    _absorb_short_self_loops(graph)
    return graph


# A self-loop whose chain is at most this long is a 2x2-remnant artifact of
# the thinning kernel at a junction cluster, not a real loop branch.
_MAX_ARTIFACT_SELF_LOOP_LEN = 3


def _absorb_short_self_loops(graph: SkeletonGraph) -> None:
    """Fold artifact self-loops back into their junction node.

    Zhang-Suen can leave a 2x2 block at a junction; its skeleton graph then
    shows a length-3 self-loop (cluster pixel -> one degree-2 pixel -> cluster
    pixel). Those pixels belong to the junction, so the edge is removed and
    its pixels are absorbed into the node. Longer self-loops are real loop
    branches and are kept.
    """
    kept: list[Edge] = []
    for e in graph.edges:
        if (
            e.node_a == e.node_b
            and len(e.pixels) <= _MAX_ARTIFACT_SELF_LOOP_LEN
            and graph.nodes[e.node_a].kind == "junction"
        ):
            node = graph.nodes[e.node_a]
            for p in e.pixels:
                if p not in node.pixels:
                    node.pixels.append(p)
            continue
        e.id = len(kept)
        kept.append(e)
    graph.edges = kept
