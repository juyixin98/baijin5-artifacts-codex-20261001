"""Influence-function variance: hand-computed values and cluster units."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import ComputationFailure, InputError
from aipw.influence import cluster_variance, iid_variance


def test_iid_variance_matches_bessel_formula():
    scores = np.array([1.0, 2.0, 3.0, 4.0])
    point = scores.mean()
    res = iid_variance(scores, point)
    s2 = np.sum((scores - point) ** 2) / 3
    assert res.variance == pytest.approx(s2 / 4)
    assert res.se == pytest.approx(np.sqrt(s2 / 4))
    assert res.independent_units == 4
    assert res.clustered is False
    # 95% normal CI
    assert res.ci_lower == pytest.approx(point - 1.9599639845 * res.se)
    assert res.ci_upper == pytest.approx(point + 1.9599639845 * res.se)


def test_cluster_variance_hand_computed():
    # two clusters of two rows: C1 = 1+3 = 4, C2 = 2+8 = 10, Cbar = 7
    scores = np.array([1.0, 3.0, 2.0, 8.0])
    groups = np.array([0, 0, 1, 1])
    point = scores.mean()
    res = cluster_variance(scores, point, groups)
    # independent units are G=2 clusters:
    # V = G * ((4-7)^2 + (10-7)^2) / (4^2 * (G-1)) = 2*18/16 = 2.25
    assert res.variance == pytest.approx(2.25)
    assert res.se == pytest.approx(np.sqrt(2.25))
    assert res.independent_units == 2
    assert res.clustered is True
    assert res.small_cluster_warning is True
    # t critical with 1 df is huge; CI must use it (12.706...), not 1.96
    tcrit = 12.706204736
    assert res.ci_upper - point == pytest.approx(tcrit * res.se, rel=1e-6)


def test_cluster_independent_units_are_clusters_not_rows(clustered):
    from aipw.contract import Config, Estimand
    from aipw.crossfit import cross_fit, make_stratified_folds
    from aipw.estimators import estimate
    cfg = Config.default()
    ds = clustered.dataset
    folds = make_stratified_folds(ds, cfg.folds, np.random.default_rng(0))
    oof = cross_fit(ds, cfg, folds)
    comp = estimate(ds.a, ds.y, oof.propensity, oof.mu0, oof.mu1,
                    cfg.trim_propensity, Estimand.ATE, False)
    cl = cluster_variance(comp.scores, comp.point, ds.clusters)
    ij = iid_variance(comp.scores, comp.point)
    assert cl.independent_units == clustered.n_clusters
    # ignoring within-cluster correlation must understate uncertainty
    assert ij.se < cl.se


def test_single_cluster_is_computation_failure():
    with pytest.raises(ComputationFailure, match="at least 2"):
        cluster_variance(np.array([1.0, 2.0]), 1.5, np.array([0, 0]))


def test_misaligned_cluster_ids_are_input_error():
    with pytest.raises(InputError, match="row-aligned"):
        cluster_variance(np.array([1.0, 2.0, 3.0]), 2.0,
                         np.array([0, 1]))


def test_variance_rejects_single_row():
    with pytest.raises(ComputationFailure):
        iid_variance(np.array([1.0]), 1.0)
