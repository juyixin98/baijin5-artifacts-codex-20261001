"""Unit tests for additional defensive branches."""

from __future__ import annotations

import numpy as np
import pytest

from app.core import reference
from app.core.config import EstimationConfig
from app.core.contracts import CovariateDeclaration
from app.core.errors import ErrorCode, EstimationError
from app.core import estimator, reference, synthetic, validation


@pytest.mark.unit
def test_reference_ols_rejects_rank_deficient_design():
    rng = np.random.default_rng(3)
    n = 50
    x = rng.normal(size=n)
    y = rng.normal(size=n)
    Z = np.column_stack([np.ones(n), x, x])  # duplicated column
    with pytest.raises(np.linalg.LinAlgError):
        reference.ols_fit(y, Z)


@pytest.mark.unit
def test_covariate_column_count_mismatch_is_invalid_request():
    ds = synthetic.generate("balanced", n=200, seed=9)
    decls = [CovariateDeclaration("x_pre", True), CovariateDeclaration("x_irrelevant", True)]
    X_wrong = ds.covariates["x_pre"].reshape(-1, 1)  # 1 column vs 2 declarations
    with pytest.raises(EstimationError) as exc:
        validation.validate_arrays(
            ds.unit_id, ds.treatment.astype(float), ds.outcome, X_wrong, decls
        )
    assert exc.value.code is ErrorCode.INVALID_REQUEST
