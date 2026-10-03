"""Graph construction: neighbor topology, hand-computed reduction, limits."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from graphcut.energy import evaluate_energy
from graphcut.errors import ResourceExhaustedError
from graphcut.graph import build_graph
from graphcut.models import EnergySpec, PairwiseTerm
from graphcut.specs import neighbor_pairs
from graphcut.verify import cut_capacity

from tests.conftest import make_random_spec

LOGGER = logging.getLogger("graphcut.test")
BUILD_KW = dict(chunk_rows=1, max_pixels=1000, max_edges=10000, run_id="t")


class TestNeighborhood:
    def test_2x2_pairs_exact(self):
        assert neighbor_pairs(2, 2) == [(0, 1), (0, 2), (1, 3), (2, 3)]

    def test_no_wraparound_and_corner_degrees(self):
        pairs = neighbor_pairs(3, 3)
        degree = [0] * 9
        for p, q in pairs:
            degree[p] += 1
            degree[q] += 1
        assert degree[0] == 2  # corners touch 2 neighbors
        assert degree[4] == 4  # center touches 4
        assert len(pairs) == 12  # 3*2 horizontal + 3*2 vertical
        # no horizontal edge wraps across rows
        for p, q in pairs:
            if q == p + 1:
                assert (p % 3) == (q % 3) - 1


class TestHandComputedReduction:
    """2x1 spec reduced by hand in the module docs; verified here."""

    def spec(self) -> EnergySpec:
        return EnergySpec(
            width=2,
            height=1,
            unary0=np.array([[1.0, 2.0]]),
            unary1=np.array([[3.0, 0.5]]),
            pairwise=(PairwiseTerm(0, 1, 0.0, 1.0, 1.0, 0.0),),
        )

    def test_constant_and_edges(self):
        graph = build_graph(self.spec(), logger=LOGGER, **BUILD_KW)
        s, t = graph.source, graph.sink
        edges = sorted(
            (int(u), int(v), round(float(c), 9))
            for u, v, c in zip(graph.src, graph.dst, graph.cap)
        )
        assert edges == sorted([
            (0, 1, 2.0),   # pairwise edge weight v01+v10-v00-v11
            (s, 0, 3.0),   # d1(0)-d0(0) = (3+1) - 1
            (1, t, 2.5),   # d0(1)-d1(1) = 2 - (0.5-1)
        ])
        assert graph.constant == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "labeling,expected_energy",
        [([0, 0], 3.0), ([0, 1], 2.5), ([1, 0], 6.0), ([1, 1], 3.5)],
    )
    def test_cut_equals_energy_minus_constant(self, labeling, expected_energy):
        spec = self.spec()
        graph = build_graph(spec, logger=LOGGER, **BUILD_KW)
        labels = np.array(labeling, dtype=np.int8)
        assert graph.constant + cut_capacity(graph, labels) == pytest.approx(
            expected_energy
        )
        # and the independent evaluator agrees on the same number
        assert evaluate_energy(
            spec, labels.reshape(1, 2)
        ).total == pytest.approx(expected_energy)


class TestEnergyIdentityProperty:
    """constant + cut_capacity(labeling) == energy(labeling) for random
    specs and random labelings — the graph must encode the energy exactly."""

    def test_random_specs(self, rng):
        for _ in range(5):
            spec = make_random_spec(rng, 3, 3)
            graph = build_graph(spec, logger=LOGGER, **BUILD_KW)
            for _ in range(10):
                labeling = rng.integers(0, 2, size=9).astype(np.int8)
                energy = evaluate_energy(spec, labeling.reshape(3, 3)).total
                assert graph.constant + cut_capacity(
                    graph, labeling
                ) == pytest.approx(energy, abs=1e-9)


class TestResourceLimits:
    def test_too_many_pixels(self):
        spec = make_random_spec(np.random.default_rng(0), 4, 4)
        with pytest.raises(ResourceExhaustedError) as exc:
            build_graph(spec, logger=LOGGER, **{**BUILD_KW, "max_pixels": 4})
        assert exc.value.code == "too_many_pixels"

    def test_too_many_edges(self):
        spec = make_random_spec(np.random.default_rng(0), 3, 3)
        with pytest.raises(ResourceExhaustedError) as exc:
            build_graph(spec, logger=LOGGER, **{**BUILD_KW, "max_edges": 5})
        assert exc.value.code == "too_many_edges"

    def test_cancellation_at_chunk_boundary(self):
        spec = make_random_spec(np.random.default_rng(0), 3, 3)
        from graphcut.errors import ComputationError

        with pytest.raises(ComputationError) as exc:
            build_graph(
                spec, logger=LOGGER, should_abort=lambda: True, **BUILD_KW
            )
        assert exc.value.code == "job_cancelled"
