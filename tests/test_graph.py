"""Skeleton-graph tests: nodes, edges, and preserved pixel chains.

Reference graphs are hand-derived:
- diamond loop: 12 pixels, all degree 2 -> 0 nodes, 1 cyclic edge.
- fork fixture: junction {(10,10)}, endpoints (2,10),(16,4),(16,16),
  edge chain lengths 9, 7, 7.
- plus (7x7): the 5-pixel inner plus clusters into one junction; 4
  endpoints, 4 edges with 2-pixel chains.
- line 1x5: 2 endpoints, 1 edge with the full 5-pixel chain.
"""

from __future__ import annotations

import numpy as np

from app.graph import skeleton_graph
from app.kernel import thin
from app.samples import fork, line_1x5, plus_7x7, ring


def _diamond_loop() -> np.ndarray:
    img = np.zeros((7, 7), dtype=np.uint8)
    for r in range(7):
        for c in range(7):
            if abs(r - 3) + abs(c - 3) == 3:
                img[r, c] = 1
    return img


def test_diamond_loop_is_single_cyclic_edge() -> None:
    graph = skeleton_graph(_diamond_loop())
    assert len(graph.nodes) == 0
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.cyclic
    assert edge.node_ids == (None, None)
    assert len(edge.pixels) == 12


def test_fork_graph_structure_and_chains() -> None:
    graph = skeleton_graph(fork())
    assert len(graph.junctions) == 1
    assert graph.junctions[0].pixels == ((10, 10),)
    assert {p for n in graph.endpoints for p in n.pixels} == {
        (2, 10), (16, 4), (16, 16),
    }
    assert len(graph.edges) == 3
    assert sorted(len(e.pixels) for e in graph.edges) == [7, 7, 9]
    for edge in graph.edges:
        assert edge.pixels[0] == (10, 10) or edge.pixels[-1] == (10, 10)


def test_plus_graph() -> None:
    graph = skeleton_graph(plus_7x7())
    # Inner plus pixels are diagonally adjacent to their neighbours, so all
    # five have degree >= 3 and cluster into a single junction node.
    assert len(graph.junctions) == 1
    assert set(graph.junctions[0].pixels) == {
        (2, 3), (3, 2), (3, 3), (3, 4), (4, 3),
    }
    assert len(graph.endpoints) == 4
    assert {p for n in graph.endpoints for p in n.pixels} == {
        (1, 3), (3, 1), (3, 5), (5, 3),
    }
    assert len(graph.edges) == 4
    assert all(len(e.pixels) == 2 for e in graph.edges)


def test_line_graph_keeps_full_pixel_chain() -> None:
    graph = skeleton_graph(line_1x5())
    assert len(graph.endpoints) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].pixels == ((0, 0), (0, 1), (0, 2), (0, 3), (0, 4))


def test_edge_chains_cover_every_skeleton_pixel_exactly_once() -> None:
    image = fork()
    graph = skeleton_graph(image)
    node_pixels = {p for n in graph.nodes for p in n.pixels}
    chain_pixels = [p for e in graph.edges for p in e.pixels if p not in node_pixels]
    covered = node_pixels | set(chain_pixels)
    assert covered == {tuple(p) for p in np.argwhere(image)}
    assert len(chain_pixels) == len(set(chain_pixels))  # no double-counting


def test_thinned_ring_maps_to_cycle() -> None:
    skeleton = thin(ring()).skeleton
    graph = skeleton_graph(skeleton)
    # A closed-loop skeleton has no endpoints; every pixel lies on a cycle
    # or on edges between junctions.
    assert len(graph.endpoints) == 0
    assert len(graph.edges) >= 1
