"""Unit tests for input validation: every failure asserts a concrete category."""
from __future__ import annotations

import pytest

from app.core.contracts import (
    ErrorCode,
    EstimationError,
    MissingPolicy,
    ZeroVariancePolicy,
)
from tests.conftest import hand_dataset, prepare


def _payload(**data_overrides):
    payload = hand_dataset()
    if data_overrides:
        payload["data"].update(data_overrides)
    return payload


def test_empty_data_is_rejected(case_log):
    payload = _payload(y=[], t=[], x=[])
    case_log("empty columns", n_rows=0)
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.EMPTY_DATA


def test_unequal_column_lengths_are_rejected():
    payload = _payload(x=[1.0, 2.0])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD
    assert exc.value.details["lengths"] == {"x": 2}


def test_missing_outcome_column():
    payload = hand_dataset()
    payload["outcome_column"] = "nope"
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD


def test_unknown_covariate_is_typed():
    payload = hand_dataset()
    payload["covariates"] = ["x", "ghost"]
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.UNKNOWN_COVARIATE
    assert exc.value.details["missing_columns"] == ["ghost"]


def test_duplicate_covariate_is_typed():
    payload = hand_dataset()
    payload["covariates"] = ["x", "x"]
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.DUPLICATE_COLUMN


def test_non_binary_treatment_reports_row():
    payload = _payload(t=[0, 0, 0, 0, 1, 2, 1, 1])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.NON_BINARY_TREATMENT
    assert exc.value.details["row"] == 5


def test_null_treatment_assignment_is_rejected_not_imputed():
    payload = _payload(t=[0, 0, None, 0, 1, 1, 1, 1])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD
    assert exc.value.details["row"] == 2


def test_single_arm_is_rejected():
    payload = _payload(t=[0, 0, 0, 0, 0, 0, 0, 0])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.MISSING_ARM
    assert exc.value.details["n_treatment"] == 0


def test_non_numeric_value_reports_cell():
    payload = _payload(x=[1.0, 2.0, "oops", 4.0, 5.0, 6.0, 7.0, 8.0])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD
    assert "row 2" in str(exc.value)


def test_non_finite_value_rejected():
    payload = _payload(x=[1.0, 2.0, float("inf"), 4.0, 5.0, 6.0, 7.0, 8.0])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD


def test_missing_values_fail_by_default():
    payload = _payload(x=[1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.MISSING_VALUES_PRESENT
    assert exc.value.details["rows_with_missing"] >= 1


def test_complete_cases_reports_dropped_rows():
    payload = _payload(x=[1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    prepared = prepare(payload, missing_policy=MissingPolicy.COMPLETE_CASES)
    assert prepared.n_rows == 8
    assert prepared.n_complete_rows == 7
    assert prepared.n_dropped_rows == 1
    assert "complete_cases" in prepared.warnings[0]


def test_mean_imputation_fills_without_dropping_rows():
    payload = _payload(x=[1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    prepared = prepare(payload, missing_policy=MissingPolicy.MEAN_IMPUTE)
    assert prepared.n_dropped_rows == 0
    # Mean of observed values (1,3,4,5,6,7,8) = 34/7
    assert prepared.x[1, 0] == pytest.approx(34.0 / 7.0)


def test_zero_variance_covariate_dropped_by_default_and_flagged():
    payload = _payload(z=[9.0] * 8)
    payload["covariates"] = ["x", "z"]
    prepared = prepare(payload)
    assert prepared.dropped_covariates == ("z",)
    assert prepared.covariate_names == ("x",)


def test_zero_variance_covariate_fail_policy():
    payload = _payload(z=[9.0] * 8)
    payload["covariates"] = ["z"]
    with pytest.raises(EstimationError) as exc:
        prepare(payload, zero_variance_policy=ZeroVariancePolicy.FAIL)
    assert exc.value.code is ErrorCode.ZERO_VARIANCE_COVARIATE


def test_all_missing_covariate_under_mean_impute_is_typed_not_500():
    # Review H1: every value null. mean_impute has no observed mean; the
    # column must be rejected/dropped as zero variance, never reach numpy.
    payload = _payload(z=[None] * 8)
    payload["covariates"] = ["z"]
    prepared = prepare(payload, missing_policy=MissingPolicy.MEAN_IMPUTE)
    assert prepared.dropped_covariates == ("z",)
    assert prepared.covariate_names == ()


def test_all_missing_covariate_fail_policy_is_zero_variance_error():
    payload = _payload(z=[None] * 8)
    payload["covariates"] = ["z"]
    with pytest.raises(EstimationError) as exc:
        prepare(payload,
                missing_policy=MissingPolicy.MEAN_IMPUTE,
                zero_variance_policy=ZeroVariancePolicy.FAIL)
    assert exc.value.code is ErrorCode.ZERO_VARIANCE_COVARIATE
    assert exc.value.details["observed"] == 0


def test_zero_variance_is_scale_relative():
    # Review M3: genuinely varying tiny-scale column is KEPT...
    payload_small = _payload(z=[1e-10 + i * 1e-12 for i in range(8)])
    payload_small["covariates"] = ["z"]
    kept = prepare(payload_small)
    assert kept.covariate_names == ("z",)
    # ...while a near-constant on a huge scale is DROPPED.
    payload_big = _payload(z=[1e9 + (i % 2) * 1e-7 for i in range(8)])
    payload_big["covariates"] = ["z"]
    dropped = prepare(payload_big)
    assert dropped.dropped_covariates == ("z",)


def test_arm_of_size_one_after_prep_is_insufficient_sample():
    # Review L4: drop rows until one arm has a single unit.
    payload = _payload()
    payload["data"]["y"] = payload["data"]["y"][:5]
    payload["data"]["t"] = payload["data"]["t"][:5]
    payload["data"]["x"] = payload["data"]["x"][:5]
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.INSUFFICIENT_SAMPLE


def test_zero_variance_outcome_is_fatal():
    payload = _payload(y=[5.0] * 8)
    with pytest.raises(EstimationError) as exc:
        prepare(payload)
    assert exc.value.code is ErrorCode.ZERO_VARIANCE_OUTCOME
