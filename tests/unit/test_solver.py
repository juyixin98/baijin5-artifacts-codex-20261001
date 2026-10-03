"""Solver kernel tests: hand-computed flows, SciPy cross-check, cut."""

from __future__ import annotations

import logging

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_flow

from graphcut.graph import GraphData, build_graph
from graphcut.models import EnergySpec
from graphcut.solver import solve_maxflow
from graphcut.verify import cut_capacity

from tests.conftest import make_random_spec

LOGGER = logging.getLogger("graphcut.test")
BUILD_KW = dict(chunk_rows=2, max_pixels=100000, max_edges=1000000,
                run_id="t")


def hand_graph() -> GraphData:
    """Classic 4-node network with known max-flow 5 (computed by hand).

    Pixels a=0, b=1; source s=2, sink t=3 (GraphData convention: source and
    sink are the last two nodes). Edges: s->a 3, s->b 2, a->b 1, a->t 2,
    b->t 3. Bottleneck cuts: {s} gives 3+2=5; {s,a,b} gives 2+3=5.
    Max-flow = 5.
    """
    return GraphData(
        num_nodes=4,
        source=2,
        sink=3,
        src=np.array([2, 2, 0, 0, 1]),
        dst=np.array([0, 1, 1, 3, 3]),
        cap=np.array([3.0, 2.0, 1.0, 2.0, 3.0]),
        constant=0.0,
        seed_weight=0.0,
    )


class TestHandComputedFlow:
    def test_flow_value(self):
        out = solve_maxflow(hand_graph())
        assert out.flow_value == pytest.approx(5.0)

    def test_cut_matches_flow(self):
        graph = hand_graph()
        out = solve_maxflow(graph)
        assert cut_capacity(graph, out.labeling) == pytest.approx(5.0)


class TestAgainstScipy:
    """Our Dinic vs SciPy's independent implementation on random graphs."""

    def test_random_graphs_agree(self, rng):
        for _ in range(5):
            spec = make_random_spec(rng, 4, 4)
            graph = build_graph(spec, logger=LOGGER, **BUILD_KW)
            ours = solve_maxflow(graph).flow_value
            scaled = np.rint(graph.cap * 1_000_000).astype(np.int64)
            matrix = csr_matrix(
                (scaled, (graph.src, graph.dst)),
                shape=(graph.num_nodes, graph.num_nodes),
            )
            theirs = maximum_flow(
                matrix, graph.source, graph.sink
            ).flow_value / 1_000_000
            assert ours == pytest.approx(theirs, abs=1e-3)


class TestZeroSmoothing:
    """With zero smoothness every pixel is independent: the optimum is the
    per-pixel argmin of the data terms (reference computed by hand)."""

    def test_labeling_equals_argmin(self, rng):
        unary0 = rng.uniform(0.0, 5.0, (3, 4))
        unary1 = rng.uniform(0.0, 5.0, (3, 4))
        spec = EnergySpec(
            width=4, height=3, unary0=unary0, unary1=unary1, pairwise=()
        )
        graph = build_graph(spec, logger=LOGGER, **BUILD_KW)
        out = solve_maxflow(graph)
        expected = (unary1 < unary0).astype(np.int8).reshape(-1)
        strict = unary1.reshape(-1) != unary0.reshape(-1)
        np.testing.assert_array_equal(out.labeling[strict], expected[strict])
        assert out.flow_value + graph.constant == pytest.approx(
            np.minimum(unary0, unary1).sum()
        )


class TestKnownOptimum:
    def test_2x1_optimum(self):
        # hand-computed: energies 3.0 / 2.5 / 6.0 / 3.5 -> optimum (0,1)=2.5
        from graphcut.models import PairwiseTerm

        spec = EnergySpec(
            width=2,
            height=1,
            unary0=np.array([[1.0, 2.0]]),
            unary1=np.array([[3.0, 0.5]]),
            pairwise=(PairwiseTerm(0, 1, 0.0, 1.0, 1.0, 0.0),),
        )
        graph = build_graph(spec, logger=LOGGER, **BUILD_KW)
        out = solve_maxflow(graph)
        np.testing.assert_array_equal(out.labeling, [0, 1])
        assert graph.constant + out.flow_value == pytest.approx(2.5)
