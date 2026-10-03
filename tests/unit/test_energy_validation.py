"""Contract validation and independent energy evaluation tests."""

from __future__ import annotations

import numpy as np
import pytest

from graphcut.energy import evaluate_energy, validate_pairwise, validate_spec
from graphcut.errors import InputValidationError
from graphcut.models import EnergySpec, PairwiseTerm


def tiny_spec(**overrides) -> EnergySpec:
    base = dict(
        width=2,
        height=1,
        unary0=np.array([[1.0, 2.0]]),
        unary1=np.array([[3.0, 0.5]]),
        pairwise=(PairwiseTerm(0, 1, 0.0, 1.0, 1.0, 0.0),),
    )
    base.update(overrides)
    return EnergySpec(**base)


class TestEvaluateEnergy:
    def test_hand_computed_labeling_00(self):
        # labels (0,0): data = 1.0 + 2.0, smoothness = v00 = 0
        energy = evaluate_energy(tiny_spec(), np.array([[0, 0]]))
        assert energy.data == pytest.approx(3.0)
        assert energy.smoothness == pytest.approx(0.0)
        assert energy.total == pytest.approx(3.0)

    def test_hand_computed_labeling_01(self):
        # labels (0,1): data = 1.0 + 0.5, smoothness = v01 = 1.0
        energy = evaluate_energy(tiny_spec(), np.array([[0, 1]]))
        assert energy.data == pytest.approx(1.5)
        assert energy.smoothness == pytest.approx(1.0)
        assert energy.total == pytest.approx(2.5)

    def test_hand_computed_labeling_10(self):
        # labels (1,0): data = 3.0 + 2.0, smoothness = v10 = 1.0
        energy = evaluate_energy(tiny_spec(), np.array([[1, 0]]))
        assert energy.total == pytest.approx(6.0)

    def test_rejects_non_binary_labeling(self):
        with pytest.raises(InputValidationError) as exc:
            evaluate_energy(tiny_spec(), np.array([[0, 2]]))
        assert exc.value.code == "bad_labeling_values"

    def test_rejects_wrong_shape(self):
        with pytest.raises(InputValidationError) as exc:
            evaluate_energy(tiny_spec(), np.array([0, 1]))
        assert exc.value.code == "bad_labeling_shape"


class TestUnaryValidation:
    def test_negative_data_term_rejected(self):
        spec = tiny_spec(unary0=np.array([[-0.25, 2.0]]))
        with pytest.raises(InputValidationError) as exc:
            validate_spec(spec)
        assert exc.value.code == "negative_data_term"
        assert exc.value.category.value == "input"

    def test_nan_unary_rejected(self):
        spec = tiny_spec(unary1=np.array([[np.nan, 0.5]]))
        with pytest.raises(InputValidationError) as exc:
            validate_spec(spec)
        assert exc.value.code == "bad_unary_values"

    def test_shape_mismatch_rejected(self):
        spec = tiny_spec(unary1=np.array([[0.5]]))
        with pytest.raises(InputValidationError) as exc:
            validate_spec(spec)
        assert exc.value.code == "bad_unary_shape"


class TestPairwiseValidation:
    def test_negative_smoothness_rejected(self):
        term = PairwiseTerm(0, 1, -0.1, 1.0, 1.0, 0.0)
        with pytest.raises(InputValidationError) as exc:
            validate_pairwise(term)
        assert exc.value.code == "negative_smoothness_term"

    def test_non_submodular_rejected_not_absolutized(self):
        # v00 + v11 = 2.0 > v01 + v10 = 0.2: supermodular, must be rejected
        term = PairwiseTerm(0, 1, 1.0, 0.1, 0.1, 1.0)
        with pytest.raises(InputValidationError) as exc:
            validate_pairwise(term)
        assert exc.value.code == "non_submodular_pairwise"
        assert exc.value.category.value == "input"
        assert "2.0" in exc.value.message  # reason names the actual sums

    def test_submodular_equality_accepted(self):
        # v00 + v11 == v01 + v10: boundary case is still submodular
        validate_pairwise(PairwiseTerm(0, 1, 1.0, 1.5, 1.5, 1.0))

    def test_bad_endpoint_rejected(self):
        spec = tiny_spec(pairwise=(PairwiseTerm(0, 7, 0.0, 1.0, 1.0, 0.0),))
        with pytest.raises(InputValidationError) as exc:
            validate_spec(spec)
        assert exc.value.code == "bad_pairwise_endpoint"
