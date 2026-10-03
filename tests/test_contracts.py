"""Contract validation tests: each rule maps to a specific error category."""

import numpy as np
import pytest

from fir_backend.contracts import (
    EstimateParams,
    validate_params,
    validate_request,
    validate_sample_block,
)
from fir_backend.errors import (
    ErrorCategory,
    InputValidationError,
    ResourceExhaustedError,
)


def test_mismatched_lengths_are_input_errors():
    with pytest.raises(InputValidationError) as excinfo:
        validate_sample_block(np.ones(10), np.ones(11))
    assert excinfo.value.category is ErrorCategory.INPUT


def test_nan_and_inf_rejected():
    for bad in (np.array([1.0, np.nan, 3.0]), np.array([1.0, np.inf, 3.0])):
        with pytest.raises(InputValidationError):
            validate_sample_block(bad, np.ones(3))


def test_two_dimensional_input_rejected():
    with pytest.raises(InputValidationError):
        validate_sample_block(np.ones((4, 2)), np.ones(4))


def test_empty_input_rejected():
    with pytest.raises(InputValidationError):
        validate_sample_block(np.array([]), np.array([]))


def test_sample_limit_is_resource_error_not_input_error():
    with pytest.raises(ResourceExhaustedError) as excinfo:
        validate_sample_block(np.ones(100), np.ones(100), max_samples=50)
    assert excinfo.value.category is ErrorCategory.RESOURCE


def test_model_order_bounds():
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=0), n_samples=100)
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=-3), n_samples=100)
    # Fewer samples than model_order + 1 cannot fit.
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=8), n_samples=8)


def test_delay_must_be_integer_and_within_samples():
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=4, delay=100), n_samples=100)
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=4, delay=-100), n_samples=100)


def test_regularization_must_be_nonnegative_finite():
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=4, regularization=-1.0), n_samples=100)
    with pytest.raises(InputValidationError):
        validate_params(
            EstimateParams(model_order=4, regularization=float("nan")), n_samples=100
        )


def test_holdout_fraction_bounds():
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=4, holdout_fraction=0.95), n_samples=100)
    with pytest.raises(InputValidationError):
        validate_params(EstimateParams(model_order=4, holdout_fraction=-0.1), n_samples=100)


def test_valid_request_round_trips():
    request = validate_request(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [0.5, 1.0, 1.5, 2.0, 2.5],
        EstimateParams(model_order=2, delay=0, regularization=1e-3, holdout_fraction=0.2),
    )
    assert request.samples.n_samples == 5
    assert request.params.model_order == 2
