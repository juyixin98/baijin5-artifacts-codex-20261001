"""Unit tests for the s-t graph builder.

The central property tested here is *energy-graph correspondence*: for
every labeling, the capacity of the induced s-t cut (computed directly
from the arc list, in this file) must equal the energy of that labeling
(computed by the independent evaluator).
"""

import itertools

import numpy as np
import pytest

from graphcut.config import Settings
from graphcut.contracts import Seed, validate_pairwise
from graphcut.energy import evaluate_energy
from graphcut.errors import ResourceExhaustedError
from graphcut.graph import build_st_graph


def cut_capacity_from_arcs(graph, labels_flat):
    """Test-side cut value: arcs crossing S->T plus the graph constant."""
    in_s = np.zeros(graph.num_nodes, dtype=bool)
    in_s[graph.source] = True
    in_s[: graph.num_pixels] = labels_flat.astype(bool)
    cross = in_s[graph.edges_from] & ~in_s[graph.edges_to]
    return float(graph.edges_cap[cross].sum()) + graph.constant


def random_submodular_table(rng):
    b, c = rng.uniform(0.0, 3.0, size=2)
    a = rng.uniform(0.0, b + c)
    d = rng.uniform(0.0, b + c - a)
    return validate_pairwise(float(a), float(b), float(c), float(d))


class TestEnergyGraphCorrespondence:
    @pytest.mark.parametrize("height,width", [(1, 2), (2, 2), (2, 3)])
    def test_all_labelings_cut_equals_energy(self, spec_factory, seeded_rng,
                                             test_log, height, width):
        rng = seeded_rng
        pairwise = random_submodular_table(rng)
        spec = spec_factory(
            height=height, width=width,
            unary0=rng.uniform(0.0, 4.0, size=(height, width)),
            unary1=rng.uniform(0.0, 4.0, size=(height, width)),
            pairwise=pairwise,
        )
        graph = build_st_graph(spec)
        n = spec.num_pixels
        worst = 0.0
        for bits in itertools.product([0, 1], repeat=n):
            labels = np.array(bits, dtype=np.int8).reshape(height, width)
            energy = evaluate_energy(spec, labels).total
            cut = cut_capacity_from_arcs(graph, labels.ravel())
            worst = max(worst, abs(energy - cut))
            assert cut == pytest.approx(energy, abs=1e-9, rel=1e-9)
        test_log.info(
            "correspondence.ok shape=(%d,%d) labelings=%d worst_gap=%.3e "
            "pairwise=(%.4f,%.4f,%.4f,%.4f)",
            height, width, 2 ** n, worst,
            pairwise.v00, pairwise.v01, pairwise.v10, pairwise.v11)


class TestZeroSmoothness:
    def test_zero_potts_adds_no_pairwise_arcs(self, spec_factory):
        spec = spec_factory(
            height=2, width=3,
            unary0=np.ones((2, 3)), unary1=np.ones((2, 3)),
            pairwise=validate_pairwise(0.0, 0.0, 0.0, 0.0),
        )
        graph = build_st_graph(spec)
        # Only t-links remain: at most one arc per pixel, none between pixels.
        pixel_arcs = (graph.edges_from < graph.num_pixels) & \
                     (graph.edges_to < graph.num_pixels)
        assert not pixel_arcs.any()

    def test_zero_smoothness_solution_is_argmin(self, spec_factory):
        unary0 = np.array([[0.5, 2.0], [3.0, 0.25]])
        unary1 = np.array([[1.5, 0.5], [0.5, 4.0]])
        spec = spec_factory(height=2, width=2, unary0=unary0, unary1=unary1,
                            pairwise=validate_pairwise(0.0, 0.0, 0.0, 0.0))
        from graphcut.service import run_segmentation
        result = run_segmentation(spec)
        expected = (unary1 < unary0).astype(int)
        assert result.labels.astype(int).tolist() == expected.tolist()
        assert result.certificate.energy.total == pytest.approx(
            float(np.minimum(unary0, unary1).sum()))


class TestBigM:
    def test_big_m_is_finite_capacity_plus_one(self, spec_factory):
        spec = spec_factory(
            height=1, width=2,
            unary0=[[0.3, 0.4]], unary1=[[0.1, 0.2]],
            pairwise=validate_pairwise(0.0, 2.0, 2.0, 0.0),
            seeds=[Seed(0, 0, 1), Seed(0, 1, 0)],
        )
        graph = build_st_graph(spec)
        non_seed = graph.edges_cap[:-2]  # seed arcs appended last
        assert graph.num_seed_arcs == 2
        assert graph.big_m == pytest.approx(float(non_seed.sum()) + 1.0)
        assert (graph.edges_cap[-2:] == graph.big_m).all()

    def test_capacity_overflow_rejected(self, spec_factory, test_log):
        spec = spec_factory(
            height=1, width=2,
            unary0=[[1e300, 0.0]], unary1=[[0.0, 0.0]],
            seeds=[Seed(0, 1, 1)],
        )
        with pytest.raises(ResourceExhaustedError) as excinfo:
            build_st_graph(spec)
        err = excinfo.value
        test_log.info("rejected.capacity_overflow code=%s details=%s",
                      err.code, err.details)
        assert err.code == "CAPACITY_OVERFLOW"

    def test_no_seeds_means_zero_big_m(self, spec_factory):
        spec = spec_factory(height=1, width=2,
                            unary0=[[0.1, 0.2]], unary1=[[0.3, 0.4]])
        graph = build_st_graph(spec)
        assert graph.big_m == 0.0
        assert graph.num_seed_arcs == 0


class TestChunkedBuild:
    def test_chunked_build_matches_unchunked(self, spec_factory, seeded_rng):
        rng = seeded_rng
        spec = spec_factory(
            height=5, width=4,
            unary0=rng.uniform(0.0, 3.0, size=(5, 4)),
            unary1=rng.uniform(0.0, 3.0, size=(5, 4)),
            pairwise=random_submodular_table(rng),
        )
        whole = build_st_graph(spec)
        progress = []
        chunked = build_st_graph(spec, chunk_rows=2,
                                 on_chunk=lambda d, t: progress.append((d, t)))

        def arc_multiset(g):
            arcs = np.stack([g.edges_from, g.edges_to, g.edges_cap], axis=1)
            return arcs[np.lexsort(arcs.T[::-1])]

        np.testing.assert_allclose(arc_multiset(whole), arc_multiset(chunked),
                                   rtol=0, atol=1e-12)
        assert whole.constant == pytest.approx(chunked.constant)
        assert progress[-1][0] == progress[-1][1] == 3  # ceil(5/2) chunks
