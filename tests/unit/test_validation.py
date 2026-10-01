"""Unit tests for the input contract failure taxonomy."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import EstimationConfig
from app.core.contracts import CovariateDeclaration
from app.core.errors import ErrorCode, EstimationError
from app.core import estimator, synthetic


def _estimate_with(unit_id, treatment, outcome, covs, decls):
    return estimator.estimate(
        "exp", np.asarray(unit_id), np.asarray(treatment, dtype=float),
        np.asarray(outcome, dtype=float), covs, decls,
        EstimationConfig(), "unit-validation",
    )


@pytest.mark.unit
def test_empty_data_is_rejected():
    decl = [CovariateDeclaration("x", True)]
    with pytest.raises(EstimationError) as exc:
        _estimate_with(
            np.array([]), np.array([]), np.array([]),
            {"x": np.array([]).reshape(0, 1)}, decl,
        )
    assert exc.value.code is ErrorCode.EMPTY_DATA


@pytest.mark.unit
def test_non_binary_treatment_is_rejected():
    ds = synthetic.generate("balanced", n=300, seed=1)
    t = ds.treatment.copy()
    t[0] = 2
    with pytest.raises(EstimationError) as exc:
        _estimate_with(ds.unit_id, t, ds.outcome, ds.covariates, ds.declarations)
    assert exc.value.code is ErrorCode.TREATMENT_NOT_BINARY


@pytest.mark.unit
def test_empty_arm_is_rejected():
    ds = synthetic.generate("balanced", n=300, seed=2)
    with pytest.raises(EstimationError) as exc:
        _estimate_with(
            ds.unit_id, np.zeros(len(ds.outcome)), ds.outcome,
            ds.covariates, ds.declarations,
        )
    assert exc.value.code is ErrorCode.GROUP_EMPTY


@pytest.mark.unit
def test_duplicate_unit_ids_are_rejected():
    ds = synthetic.generate("balanced", n=300, seed=3)
    ids = ds.unit_id.copy()
    ids[1] = ids[0]
    with pytest.raises(EstimationError) as exc:
        _estimate_with(ids, ds.treatment, ds.outcome, ds.covariates, ds.declarations)
    assert exc.value.code is ErrorCode.DUPLICATE_UNIT


@pytest.mark.unit
def test_missing_outcome_is_rejected():
    ds = synthetic.generate("balanced", n=300, seed=4)
    y = ds.outcome.copy()
    y[0] = np.nan
    with pytest.raises(EstimationError) as exc:
        _estimate_with(ds.unit_id, ds.treatment, y, ds.covariates, ds.declarations)
    assert exc.value.code is ErrorCode.OUTCOME_MISSING
    assert exc.value.details["n_missing"] == 1


@pytest.mark.unit
def test_collinear_covariates_are_rejected_as_rank_deficient():
    ds = synthetic.generate("balanced", n=500, seed=5)
    covs = dict(ds.covariates)
    covs["x_copy"] = ds.covariates["x_pre"]
    decls = ds.declarations + [CovariateDeclaration("x_copy", True)]
    with pytest.raises(EstimationError) as exc:
        _estimate_with(ds.unit_id, ds.treatment, ds.outcome, covs, decls)
    assert exc.value.code is ErrorCode.RANK_DEFICIENT_DESIGN
