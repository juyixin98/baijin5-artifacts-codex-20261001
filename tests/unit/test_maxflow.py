"""Unit tests for the max-flow kernel wrapper.

Reference values are hand-known (CLRS classic flow network), not produced
by the implementation under test.
"""

import numpy as np
import pytest

from graphcut.certificate import cut_capacity_of
from graphcut.graph import STGraph
from graphcut.maxflow import solve_min_cut


def make_graph(num_pixels, arcs):
    frm = np.array([a[0] for a in arcs], dtype=np.int64)
    to = np.array([a[1] for a in arcs], dtype=np.int64)
    cap = np.array([a[2] for a in arcs], dtype=np.float64)
    return STGraph(
        num_pixels=num_pixels,
        source=num_pixels,
        sink=num_pixels + 1,
        edges_from=frm, edges_to=to, edges_cap=cap,
        constant=0.0, big_m=0.0, num_seed_arcs=0,
    )


class TestClrsNetwork:
    """CLRS "Introduction to Algorithms" classic 6-node network, max flow 23.

    Nodes: s, v1, v2, v3, v4, t  ->  here v1..v4 are pixels 0..3,
    source = 4, sink = 5.
    """

    ARCS = [
        (4, 0, 16.0),  # s  -> v1
        (4, 1, 13.0),  # s  -> v2
        (0, 2, 12.0),  # v1 -> v3
        (1, 0, 4.0),   # v2 -> v1
        (1, 3, 14.0),  # v2 -> v4
        (2, 1, 9.0),   # v3 -> v2
        (2, 5, 20.0),  # v3 -> t
        (3, 2, 7.0),   # v4 -> v3
        (3, 5, 4.0),   # v4 -> t
    ]

    def test_max_flow_value_is_23(self):
        graph = make_graph(4, self.ARCS)
        result = solve_min_cut(graph)
        assert result.flow_value == pytest.approx(23.0)

    def test_min_cut_capacity_matches_flow(self):
        graph = make_graph(4, self.ARCS)
        result = solve_min_cut(graph)
        cut, cut_arcs = cut_capacity_of(graph, result.source_set)
        assert cut == pytest.approx(23.0)
        assert cut_arcs > 0
        # The known min cut: S = {s, v1, v2, v4} -> v1->v3 (12) + v4->v3 (7)
        # + v4->t (4) = 23.  v3 must be on the sink side.
        assert not result.source_set[2]
        assert result.source_set[graph.source]
        assert not result.source_set[graph.sink]


class TestDegenerateGraphs:
    def test_single_pixel_no_arcs_gives_zero_flow(self):
        graph = make_graph(1, [])
        result = solve_min_cut(graph)
        assert result.flow_value == pytest.approx(0.0)
        # Source side is just the source itself.
        assert result.source_set.tolist() == [False, True, False]

    def test_disconnected_source_and_sink(self):
        arcs = [(0, 1, 5.0)]  # pixel arc only; terminals isolated
        graph = make_graph(2, arcs)
        result = solve_min_cut(graph)
        assert result.flow_value == pytest.approx(0.0)
