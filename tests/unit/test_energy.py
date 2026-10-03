"""Unit tests for input contracts and the independent energy evaluator."""

import numpy as np
import pytest

from graphcut.config import Settings
from graphcut.contracts import Seed, build_spec, neighbor_pairs, validate_pairwise
from graphcut.energy import evaluate_energy
from graphcut.errors import (
    ErrorCategory,
    InputValidationError,
    ResourceExhaustedError,
    StateConflictError,
)


class TestPairwiseValidation:
    def test_submodular_potts_accepted(self):
        spec = validate_pairwise(0.0, 2.0, 2.0, 0.0)
        assert spec.submodular_slack == pytest.approx(4.0)

    def test_submodular_boundary_accepted(self):
        # v00 + v11 == v01 + v10 is exactly submodular (slack 0).
        spec = validate_pairwise(1.0, 2.0, 3.0, 4.0)
        assert spec.submodular_slack == pytest.approx(0.0)

    def test_non_submodular_rejected_not_repaired(self, test_log):
        # v00 + v11 = 5 > 2 = v01 + v10: supermodular, must be rejected.
        with pytest.raises(InputValidationError) as excinfo:
            validate_pairwise(0.0, 1.0, 1.0, 5.0)
        err = excinfo.value
        test_log.info("rejected.non_submodular code=%s message=%s",
                      err.code, err.message)
        assert err.code == "NON_SUBMODULAR_POTENTIAL"
        assert err.category is ErrorCategory.INPUT_VALIDATION
        # The contract forbids silent abs()/clamp repair; the error says so.
        assert "refuses to approximate" in err.message

    def test_negative_pairwise_rejected(self):
        with pytest.raises(InputValidationError) as excinfo:
            validate_pairwise(0.0, -1.0, 1.0, 0.0)
        assert excinfo.value.code == "NEGATIVE_SMOOTHNESS_TERM"

    def test_non_finite_pairwise_rejected(self):
        with pytest.raises(InputValidationError) as excinfo:
            validate_pairwise(0.0, float("inf"), 1.0, 0.0)
        assert excinfo.value.code == "NON_FINITE_SMOOTHNESS_TERM"


class TestUnaryValidation:
    def test_negative_data_term_rejected(self, spec_factory):
        with pytest.raises(InputValidationError) as excinfo:
            spec_factory(height=1, width=2,
                         unary0=[[0.0, -0.5]], unary1=[[0.0, 0.0]])
        assert excinfo.value.code == "NEGATIVE_DATA_TERM"
        assert excinfo.value.category is ErrorCategory.INPUT_VALIDATION

    def test_unary_shape_mismatch(self, spec_factory):
        with pytest.raises(InputValidationError) as excinfo:
            spec_factory(height=2, width=2,
                         unary0=[[0.0, 0.0]], unary1=[[0.0, 0.0], [0.0, 0.0]])
        assert excinfo.value.code == "UNARY_SHAPE_MISMATCH"

    def test_image_too_large_is_resource_error(self):
        settings = Settings(max_pixels=4)
        with pytest.raises(ResourceExhaustedError) as excinfo:
            build_spec(height=3, width=3,
                       unary0=np.zeros((3, 3)), unary1=np.zeros((3, 3)),
                       pairwise=validate_pairwise(0.0, 1.0, 1.0, 0.0),
                       settings=settings)
        err = excinfo.value
        assert err.code == "IMAGE_TOO_LARGE"
        assert err.category is ErrorCategory.RESOURCE_EXHAUSTED


class TestSeeds:
    def test_conflicting_seeds_are_state_conflict(self, spec_factory, test_log):
        with pytest.raises(StateConflictError) as excinfo:
            spec_factory(height=2, width=2,
                         unary0=np.zeros((2, 2)), unary1=np.zeros((2, 2)),
                         seeds=[Seed(0, 1, 0), Seed(0, 1, 1)])
        err = excinfo.value
        test_log.info("rejected.seed_conflict code=%s details=%s",
                      err.code, err.details)
        assert err.code == "SEED_CONFLICT"
        assert err.category is ErrorCategory.STATE_CONFLICT

    def test_duplicate_same_label_seeds_deduped(self, spec_factory):
        spec = spec_factory(height=2, width=2,
                            unary0=np.zeros((2, 2)), unary1=np.zeros((2, 2)),
                            seeds=[Seed(1, 1, 1), Seed(1, 1, 1), Seed(0, 0, 0)])
        assert len(spec.seeds) == 2

    def test_seed_out_of_bounds(self, spec_factory):
        with pytest.raises(InputValidationError) as excinfo:
            spec_factory(height=2, width=2,
                         unary0=np.zeros((2, 2)), unary1=np.zeros((2, 2)),
                         seeds=[Seed(2, 0, 1)])
        assert excinfo.value.code == "SEED_OUT_OF_BOUNDS"


class TestNeighborPairs:
    def test_2x2_boundary_edges_exact(self):
        # Hand-listed: 2 horizontal + 2 vertical, no wrap-around.
        assert neighbor_pairs(2, 2) == [(0, 1), (2, 3), (0, 2), (1, 3)]

    def test_3x3_edge_count(self):
        # 3 rows * 2 horizontal + 2 rows * 3 vertical = 12.
        assert len(neighbor_pairs(3, 3)) == 12

    def test_single_pixel_has_no_edges(self):
        assert neighbor_pairs(1, 1) == []

    def test_line_image_has_only_chain_edges(self):
        assert neighbor_pairs(1, 4) == [(0, 1), (1, 2), (2, 3)]


class TestEvaluateEnergy:
    def test_hand_computed_2x2(self, spec_factory):
        # Data term by hand: labels [[0,1],[1,0]] ->
        #   u0(0,0)=1 + u1(0,1)=6 + u1(1,0)=7 + u0(1,1)=4 = 18
        # Smooth (Potts w=2): all four 4-neighbour pairs differ -> 4*2 = 8
        spec = spec_factory(
            height=2, width=2,
            unary0=[[1.0, 2.0], [3.0, 4.0]],
            unary1=[[5.0, 6.0], [7.0, 8.0]],
            pairwise=validate_pairwise(0.0, 2.0, 2.0, 0.0),
        )
        labels = np.array([[0, 1], [1, 0]], dtype=np.int8)
        energy = evaluate_energy(spec, labels)
        assert energy.data == pytest.approx(18.0)
        assert energy.smooth == pytest.approx(8.0)
        assert energy.total == pytest.approx(26.0)

    def test_general_table_energy(self, spec_factory):
        # V(0,0)=1, V(0,1)=2, V(1,0)=3, V(1,1)=0.5 (slack 3.5, submodular)
        spec = spec_factory(
            height=1, width=2,
            unary0=[[0.0, 0.0]], unary1=[[0.0, 0.0]],
            pairwise=validate_pairwise(1.0, 2.0, 3.0, 0.5),
        )
        assert evaluate_energy(spec, np.array([[0, 0]])).smooth == pytest.approx(1.0)
        assert evaluate_energy(spec, np.array([[0, 1]])).smooth == pytest.approx(2.0)
        assert evaluate_energy(spec, np.array([[1, 0]])).smooth == pytest.approx(3.0)
        assert evaluate_energy(spec, np.array([[1, 1]])).smooth == pytest.approx(0.5)

    def test_labels_validation(self, spec_factory):
        spec = spec_factory(height=1, width=2,
                            unary0=[[0.0, 0.0]], unary1=[[0.0, 0.0]])
        with pytest.raises(InputValidationError):
            evaluate_energy(spec, np.array([[0, 2]]))
