"""Integration tests with hand-computed reference answers.

Every expected value below was derived by hand (shown in comments), never
by running the implementation under test.
"""

import numpy as np
import pytest

from graphcut.contracts import Seed, validate_pairwise
from graphcut.service import run_segmentation


class TestTwoPixelHandComputed:
    """1x2 image, unary0=[0.2, 3.0], unary1=[2.0, 0.1], Potts w=1.0.

    By hand over all four labelings:
        [0,0]: 0.2 + 3.0 + 0   = 3.2
        [0,1]: 0.2 + 0.1 + 1.0 = 1.3   <- optimum
        [1,0]: 2.0 + 3.0 + 1.0 = 6.0
        [1,1]: 2.0 + 0.1 + 0   = 2.1
    """

    def test_optimum(self, spec_factory, test_log):
        spec = spec_factory(
            height=1, width=2,
            unary0=[[0.2, 3.0]], unary1=[[2.0, 0.1]],
            pairwise=validate_pairwise(0.0, 1.0, 1.0, 0.0),
        )
        result = run_segmentation(spec)
        test_log.info("handcase.2px run_id=%s energy=%.6f labels=%s",
                      result.run_id, result.certificate.energy.total,
                      result.labels.tolist())
        assert result.labels.tolist() == [[0, 1]]
        assert result.certificate.energy.total == pytest.approx(1.3)
        # The graph folds constants into t-links: const = u0(p0) + u1(p1)
        # = 0.2 + 0.1 = 0.3, so the raw flow value is 1.3 - 0.3 = 1.0.
        assert result.graph_constant == pytest.approx(0.3)
        assert result.certificate.flow_value == pytest.approx(1.0)
        assert result.certificate.flow_value + result.graph_constant == \
            pytest.approx(1.3)
        assert result.certificate.verified


class TestZeroSmoothnessHandComputed:
    """2x2, w=0: solution is the per-pixel argmin.

    unary0 = [[0.5, 2.0], [3.0, 0.25]]   unary1 = [[1.5, 0.5], [0.5, 4.0]]
    argmin -> [[0, 1], [1, 0]], energy = 0.5+0.5+0.5+0.25 = 1.75
    """

    def test_argmin_labels(self, spec_factory):
        spec = spec_factory(
            height=2, width=2,
            unary0=[[0.5, 2.0], [3.0, 0.25]],
            unary1=[[1.5, 0.5], [0.5, 4.0]],
            pairwise=validate_pairwise(0.0, 0.0, 0.0, 0.0),
        )
        result = run_segmentation(spec)
        assert result.labels.tolist() == [[0, 1], [1, 0]]
        assert result.certificate.energy.total == pytest.approx(1.75)
        assert result.certificate.energy.smooth == pytest.approx(0.0)


class TestHardSeedHandComputed:
    """1x2, unary0=[0.1, 0.1], unary1=[5.0, 5.0], w=0, seed pixel1 = fg.

    Without the seed the optimum is [0,0] with energy 0.2.
    The seed forces pixel 1 to label 1: energy = 0.1 + 5.0 = 5.1.
    """

    def test_seed_overrides_data_term(self, spec_factory):
        spec = spec_factory(
            height=1, width=2,
            unary0=[[0.1, 0.1]], unary1=[[5.0, 5.0]],
            pairwise=validate_pairwise(0.0, 0.0, 0.0, 0.0),
            seeds=[Seed(0, 1, 1)],
        )
        result = run_segmentation(spec)
        assert result.labels.tolist() == [[0, 1]]
        assert result.certificate.energy.total == pytest.approx(5.1)
        assert result.certificate.seeds_satisfied
        # big_m must exceed any seed-respecting cut: the only non-seed arcs
        # are the two t-links with capacity |5.0-0.1| = 4.9 each, so
        # big_m == 4.9 + 4.9 + 1.0 == 10.8.
        assert result.big_m == pytest.approx(10.8)


class TestBoundaryAdjacencyHandComputed:
    """1x3 chain, w=10; verifies no phantom wrap-around edge exists.

    unary0 = [0, 100, 0], unary1 = [100, 0, 100].
    Labeling [0,1,0]: data 0, smooth 2*10 = 20 -> total 20.
    Any uniform labeling costs 100 in data. If a wrap-around edge between
    pixel 2 and pixel 0 existed, [0,1,0] would pay 20 (it is 0-0 there, so
    the distinguishing case is labeling [1,0,0] vs phantom edges); the
    decisive check is the exact energy value 20.0 and the arc count.
    """

    def test_chain_energy(self, spec_factory):
        spec = spec_factory(
            height=1, width=3,
            unary0=[[0.0, 100.0, 0.0]], unary1=[[100.0, 0.0, 100.0]],
            pairwise=validate_pairwise(0.0, 10.0, 10.0, 0.0),
        )
        result = run_segmentation(spec)
        assert result.labels.tolist() == [[0, 1, 0]]
        assert result.certificate.energy.total == pytest.approx(20.0)
        # 2 undirected edges -> 4 pairwise arcs + 3 t-links (sink side,
        # since u1-u0 = +100, -100, +100 -> arcs: p0->sink, source->p1,
        # p2->sink) = 7 arcs total.
        assert result.num_edges == 7
