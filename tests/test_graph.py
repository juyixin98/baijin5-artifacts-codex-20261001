"""Skeleton graph tests: node/edge structure and pixel-chain fidelity."""

from __future__ import annotations

import numpy as np

from app.graph import extract_graph
from app.kernel import thin


def _assert_chains_cover_skeleton(skel, graph):
    covered = set()
    for e in graph.edges:
        covered.update(e.pixels)
    for n in graph.nodes:
        covered.update(n.pixels)
    assert covered == {tuple(p) for p in np.argwhere(skel == 1)}


def _assert_chains_walk_on_skeleton(skel, graph):
    skel_set = {tuple(p) for p in np.argwhere(skel == 1)}
    for e in graph.edges:
        assert e.pixels, "edge chain must not be empty"
        for p in e.pixels:
            assert p in skel_set
        # consecutive chain pixels must be 8-adjacent
        for a, b in zip(e.pixels, e.pixels[1:]):
            assert max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1


def test_fork_graph_structure(fork):
    skel = thin(fork).skeleton
    g = extract_graph(skel)
    endpoints = [n for n in g.nodes if n.kind == "endpoint"]
    junctions = [n for n in g.nodes if n.kind == "junction"]
    assert len(endpoints) == 3
    assert len(junctions) == 1
    assert len(g.edges) == 3
    for e in g.edges:
        ends = {e.node_a, e.node_b}
        assert junctions[0].id in ends  # every edge touches the junction
    _assert_chains_cover_skeleton(skel, g)
    _assert_chains_walk_on_skeleton(skel, g)


def test_ring_graph_is_a_single_cycle(circle_ring):
    skel = thin(circle_ring).skeleton
    g = extract_graph(skel)
    assert [n.kind for n in g.nodes] == ["cycle_anchor"]
    assert len(g.edges) == 1
    edge = g.edges[0]
    assert edge.node_a == edge.node_b  # self-loop
    assert edge.pixels[0] == edge.pixels[-1]  # chain closes on itself
    _assert_chains_cover_skeleton(skel, g)
    _assert_chains_walk_on_skeleton(skel, g)


def test_thin_bridge_graph_is_one_edge(thin_bridge):
    g = extract_graph(thin(thin_bridge).skeleton)
    assert sorted(n.kind for n in g.nodes) == ["endpoint", "endpoint"]
    assert len(g.edges) == 1
    edge = g.edges[0]
    assert edge.pixels[0] == (4, 2) and edge.pixels[-1] == (4, 18)
    assert len(edge.pixels) == 17  # original pixel chain preserved end to end


def test_isolated_pixel_becomes_isolated_node():
    skel = np.zeros((7, 7), dtype=np.uint8)
    skel[3, 3] = 1
    g = extract_graph(skel)
    assert len(g.nodes) == 1
    assert g.nodes[0].kind == "isolated"
    assert g.edges == []


def test_edge_chain_maps_back_to_original_coordinates(fork):
    skel = thin(fork).skeleton
    g = extract_graph(skel)
    # Every node pixel must be reachable from its edges' chain endpoints.
    node_pixels = {n.id: set(n.pixels) for n in g.nodes}
    for e in g.edges:
        assert e.pixels[0] in node_pixels[e.node_a]
        assert e.pixels[-1] in node_pixels[e.node_b]
