"""Unit tests for the cut certificate: tampered results must be rejected."""

import numpy as np
import pytest

from graphcut.certificate import verify_cut
from graphcut.contracts import Seed
from graphcut.errors import ComputationError, ErrorCategory
from graphcut.graph import build_st_graph
from graphcut.maxflow import MaxFlowResult, solve_min_cut
from graphcut.service import run_segmentation


class TestCertificateAcceptsHonestSolve:
    def test_verified_certificate_fields(self, spec_factory):
        spec = spec_factory(
            height=2, width=2,
            unary0=[[0.2, 3.0], [1.0, 1.0]],
            unary1=[[2.0, 0.1], [1.0, 1.0]],
            seeds=[Seed(0, 0, 0)],
        )
        result = run_segmentation(spec)
        cert = result.certificate
        assert cert.verified
        assert cert.seeds_satisfied
        assert cert.terminals_consistent
        assert cert.abs_gap_flow_cut <= cert.tolerance
        assert cert.abs_gap_cut_energy <= cert.tolerance
        assert cert.energy.total == pytest.approx(
            cert.flow_value + result.graph_constant)


class TestCertificateRejectsTampering:
    def _honest(self, spec_factory):
        spec = spec_factory(
            height=2, width=2,
            unary0=[[0.2, 3.0], [1.0, 1.0]],
            unary1=[[2.0, 0.1], [1.0, 1.0]],
        )
        graph = build_st_graph(spec)
        flow = solve_min_cut(graph)
        return spec, graph, flow

    def test_wrong_source_set_rejected(self, spec_factory, test_log):
        spec, graph, flow = self._honest(spec_factory)
        # Move every pixel to the sink side: cut no longer matches flow.
        bad_set = np.zeros(graph.num_nodes, dtype=bool)
        bad_set[graph.source] = True
        tampered = MaxFlowResult(flow_value=flow.flow_value, source_set=bad_set)
        with pytest.raises(ComputationError) as excinfo:
            verify_cut(spec, graph, tampered, tolerance=1e-6)
        err = excinfo.value
        test_log.info("rejected.certificate code=%s details=%s",
                      err.code, err.details)
        assert err.code == "CERTIFICATE_MISMATCH"
        assert err.category is ErrorCategory.COMPUTATION_FAILURE

    def test_seed_violation_rejected(self, spec_factory):
        # Solve the *unseeded* problem, then verify against a spec that
        # demands the opposite label on a pixel the solver flipped.
        base = spec_factory(
            height=1, width=2,
            unary0=[[0.1, 0.1]], unary1=[[5.0, 5.0]],
        )
        graph = build_st_graph(base)
        flow = solve_min_cut(graph)
        assert not flow.source_set[0]  # pixel 0 prefers label 0
        seeded = spec_factory(
            height=1, width=2,
            unary0=[[0.1, 0.1]], unary1=[[5.0, 5.0]],
            seeds=[Seed(0, 0, 1)],  # demands label 1 on pixel 0
        )
        with pytest.raises(ComputationError) as excinfo:
            verify_cut(seeded, graph, flow, tolerance=1e-6)
        assert excinfo.value.code == "CERTIFICATE_MISMATCH"
        assert "hard seeds" in excinfo.value.message
