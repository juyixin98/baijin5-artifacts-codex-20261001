"""Boundary/config branch tests and end-to-end ATT."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import (
    Config, Dataset, Estimand, InputError, TrimConfig,
)
from aipw.crossfit import make_cluster_folds
from aipw.models import LogisticRegression, OLSRidge
from aipw.pipeline import run_estimate


def test_trim_bounds_must_be_ordered():
    with pytest.raises(InputError, match="trim bounds"):
        TrimConfig(enabled=True, lower=0.9, upper=0.1)
    with pytest.raises(InputError):
        TrimConfig(lower=0.0, upper=0.5)


def test_config_rejects_unknown_and_invalid_values():
    with pytest.raises(ValueError):
        Config.from_dict({"estimand": "MEDIAN"})
    with pytest.raises(InputError):
        Config.from_dict({"propensity_model": {"l2_penalty": -1.0}})
    with pytest.raises(InputError):
        Config.from_dict({"propensity_model": {"type": "random_forest"}})
    with pytest.raises(InputError):
        Config.from_dict({"outcome_models": {"type": "lasso"}})
    with pytest.raises(InputError):
        Config.from_dict({"jobs": {"storage": "postgres"}})
    with pytest.raises(InputError):
        Config(folds=1)


def test_default_config_loads_from_file():
    cfg = Config.default()
    assert cfg.folds == 5
    assert cfg.estimand is Estimand.ATE


def test_predict_before_fit_is_computation_failure():
    from aipw.contract import ComputationFailure
    with pytest.raises(ComputationFailure, match="before fit"):
        LogisticRegression().predict_proba(np.zeros((3, 2)))
    with pytest.raises(ComputationFailure, match="before fit"):
        OLSRidge().predict(np.zeros((3, 2)))


def test_ridge_path_handles_more_columns_than_rows():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(5, 8))
    y = rng.normal(size=5)
    # plain OLS is rank-deficient; ridge produces a finite fit
    model = OLSRidge(l2_penalty=1.0).fit(x, y)
    assert np.all(np.isfinite(model.predict(x)))


def test_cluster_folds_require_cluster_ids():
    rng = np.random.default_rng(0)
    ds = Dataset(x=rng.normal(size=(40, 2)),
                 a=(rng.uniform(size=40) > 0.5).astype(float),
                 y=rng.normal(size=40), clusters=None)
    with pytest.raises(InputError, match="no cluster_id"):
        make_cluster_folds(ds, 3, rng)


def test_cluster_folds_too_few_clusters(clustered):
    with pytest.raises(InputError, match="not enough clusters"):
        make_cluster_folds(clustered.dataset, 100, np.random.default_rng(0))


def test_cluster_folds_mixed_treatment_within_cluster():
    # clusters whose treatment varies member-to-member take the unstratified
    # branch but still keep each cluster inside one fold
    rng = np.random.default_rng(1)
    g, m = 30, 6
    cid = np.repeat(np.arange(g), m)
    a = (rng.uniform(size=g * m) > 0.5).astype(float)
    ds = Dataset(x=rng.normal(size=(g * m, 2)), a=a, y=rng.normal(size=g * m),
                 clusters=cid)
    folds = make_cluster_folds(ds, 3, rng)
    for c in range(g):
        assert np.unique(folds[cid == c]).size == 1


def test_end_to_end_att_recovers_truth(both_correct, known_tau):
    cfg = Config.from_dict({"estimand": "ATT"})
    res = run_estimate(both_correct.dataset, cfg, seed=5)
    assert res.estimand == "ATT"
    assert abs(res.point - known_tau) < 0.08
    assert res.ci_lower < known_tau < res.ci_upper
    # ATT has no gcomp/ipw comparators in the current contract (JSON null)
    assert np.isnan(res.ipw_point)


def test_no_intercept_outcome_path(both_correct):
    # exercise the intercept=False design branch end to end
    cfg = Config.from_dict({"outcome_models": {"intercept": False}})
    res = run_estimate(both_correct.dataset, cfg, seed=2)
    assert np.isfinite(res.point)


def test_x_must_be_two_dimensional():
    from aipw.pipeline import validate_dataset
    rng = np.random.default_rng(0)
    payload = {"x": rng.normal(size=60).tolist(),
               "a": (rng.uniform(size=60) > 0.5).astype(float).tolist(),
               "y": rng.normal(size=60).tolist()}
    with pytest.raises(InputError, match="2-D matrix"):
        validate_dataset(payload)


def test_few_distinct_clusters_rejected():
    from aipw.pipeline import validate_dataset
    rng = np.random.default_rng(0)
    n = 30
    payload = {"x": rng.normal(size=(n, 2)).tolist(),
               "a": [1.0] * 15 + [0.0] * 15,
               "y": rng.normal(size=n).tolist(),
               "cluster_id": [0] * n}
    with pytest.raises(InputError, match="2 distinct clusters"):
        validate_dataset(payload)


def test_standardization_disabled_runs_and_audits(both_correct, known_tau):
    from aipw.diagnostics import audit_scaler_stats
    from aipw.pipeline import assign_folds
    cfg = Config.from_dict({"outcome_models": {"standardize": False}})
    res = run_estimate(both_correct.dataset, cfg, seed=0)
    assert abs(res.point - known_tau) < 0.05
    folds = assign_folds(both_correct.dataset, cfg, np.random.default_rng(0))
    oof = __import__("aipw.crossfit", fromlist=["cross_fit"]).cross_fit(
        both_correct.dataset, cfg, folds)
    report = audit_scaler_stats(both_correct.dataset, folds, cfg.folds, oof,
                                standardize=False)
    assert report.passed is True
    # identity stats are recorded instead of fitted means/scales
    np.testing.assert_allclose(oof.scalers0[0].mean, [0.0, 0.0])
    np.testing.assert_allclose(oof.scalers1[0].scale, [1.0, 1.0])
