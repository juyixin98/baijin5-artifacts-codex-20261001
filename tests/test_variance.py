"""Influence-function variance and the cluster-as-independent-unit rule.

The variance math is checked two ways:

1. algebraic: per-cluster summed influence equals an explicit groupby, and
   the SE formula matches a hand-written cluster-robust denominator;
2. empirical: across replications with a high ICC, cluster-aware SEs cover
   tau at the nominal rate while row-iid SEs under-cover.
"""

from __future__ import annotations

import numpy as np

from aipw_backend.config import AipwConfig, FoldConfig, ModelConfig
from aipw_backend.contract import validate_dataset
from aipw_backend.dgp import generate_sample
from aipw_backend.kernel import estimate_aipw

TAU = 2.0
Z = 1.959963984540054


def _cfg(seed=7301):
    return AipwConfig(
        folds=FoldConfig(n_splits=5, seed=seed),
        treatment_model=ModelConfig("logistic_ridge"),
        outcome_model=ModelConfig("ols_ridge"),
    )


def test_cluster_totals_are_within_cluster_sums_of_influence():
    sample = generate_sample(
        600, seed=11, tau=TAU, n_clusters=60, cluster_size=10, icc=0.5
    )
    data = validate_dataset(
        sample.x, sample.a, sample.y, sample.cluster, n_splits=5
    )
    res = estimate_aipw(data, _cfg())
    assert res.n_clusters == 60
    ids = np.unique(sample.cluster)
    manual = np.array([res.influence[sample.cluster == g].sum() for g in ids])
    np.testing.assert_allclose(res.cluster_totals, manual, rtol=1e-10)


def test_cluster_se_matches_hand_written_formula():
    sample = generate_sample(
        800, seed=12, tau=TAU, n_clusters=80, cluster_size=10, icc=0.4
    )
    data = validate_dataset(
        sample.x, sample.a, sample.y, sample.cluster, n_splits=5
    )
    res = estimate_aipw(data, _cfg())
    psi = res.influence
    ids, inv = np.unique(sample.cluster, return_inverse=True)
    g = ids.shape[0]
    n = psi.shape[0]
    totals = np.bincount(inv, weights=psi, minlength=g)
    sizes = np.bincount(inv, minlength=g).astype(float)
    manual_var = (g / (g - 1)) * np.sum((totals - sizes * res.estimate) ** 2) / n**2
    np.testing.assert_allclose(res.se, np.sqrt(manual_var), rtol=1e-12)


def test_cluster_se_exceeds_naive_iid_se_under_within_cluster_correlation():
    sample = generate_sample(
        1500, seed=13, tau=TAU, n_clusters=150, cluster_size=10, icc=0.8,
        cluster_randomized=True,
    )
    data_c = validate_dataset(
        sample.x, sample.a, sample.y, sample.cluster, n_splits=5
    )
    data_i = validate_dataset(sample.x, sample.a, sample.y, None, n_splits=5)
    res_c = estimate_aipw(data_c, _cfg())
    res_i = estimate_aipw(data_i, _cfg())
    # Cluster-level vs row-level cross-fit folds produce slightly different
    # nuisance fits, so estimates are close rather than bit-identical.
    assert abs(res_c.estimate - res_i.estimate) < 0.03
    assert abs(res_c.estimate - TAU) < 0.2
    assert res_c.se > 1.5 * res_i.se


def test_coverage_cluster_aware_vs_row_iid_under_high_icc():
    """Cluster-RANDOMIZED design, 150 clusters, ICC 0.6.

    Cluster-aware coverage must be near 0.95 and clearly above row-iid
    coverage; treating cluster-correlated units as independent under-covers.
    """
    reps = 150
    cover_c = np.empty(reps, dtype=bool)
    cover_i = np.empty(reps, dtype=bool)
    se_ratio = np.empty(reps)
    for r in range(reps):
        sample = generate_sample(
            1500, seed=5000 + r, tau=TAU,
            n_clusters=150, cluster_size=10, icc=0.6,
            cluster_randomized=True,
        )
        res_c = estimate_aipw(
            validate_dataset(sample.x, sample.a, sample.y, sample.cluster, n_splits=5),
            _cfg(seed=900 + r),
        )
        res_i = estimate_aipw(
            validate_dataset(sample.x, sample.a, sample.y, None, n_splits=5),
            _cfg(seed=900 + r),
        )
        cover_c[r] = res_c.ci_low <= TAU <= res_c.ci_high
        cover_i[r] = res_i.ci_low <= TAU <= res_i.ci_high
        se_ratio[r] = res_c.se / res_i.se
    rate_c, rate_i = cover_c.mean(), cover_i.mean()
    assert 0.88 <= rate_c <= 0.99, rate_c
    assert rate_i < rate_c - 0.10, (rate_c, rate_i)
    assert se_ratio.mean() > 1.5


def test_cluster_level_folds_never_split_a_cluster():
    """Cross-fitting with clusters must hold entire clusters out."""
    from aipw_backend.folds import make_folds

    sample = generate_sample(
        1000, seed=15, tau=TAU, n_clusters=100, cluster_size=10, icc=0.6,
        cluster_randomized=True,
    )
    split = make_folds(
        sample.a, 5, seed=42, stratified=True, cluster=sample.cluster
    )
    for g in np.unique(sample.cluster):
        assert np.unique(split.fold_id[sample.cluster == g]).shape[0] == 1


def test_iid_influence_variance_matches_sample_sd_formula():
    sample = generate_sample(2000, seed=14, tau=TAU)
    data = validate_dataset(sample.x, sample.a, sample.y, n_splits=5)
    res = estimate_aipw(data, _cfg())
    manual = np.std(res.influence, ddof=1) / np.sqrt(data.n)
    np.testing.assert_allclose(res.se, manual, rtol=1e-12)


def test_clustered_att_uses_clusters_and_is_finite():
    from aipw_backend.config import AipwConfig as _C

    sample = generate_sample(
        1500, seed=21, tau=TAU, n_clusters=150, cluster_size=10, icc=0.6,
        cluster_randomized=True,
    )
    cfg = _C(
        folds=FoldConfig(n_splits=5, seed=77),
        treatment_model=ModelConfig("logistic_ridge"),
        outcome_model=ModelConfig("ols_ridge"),
        estimand="ATT",
    )
    data = validate_dataset(
        sample.x, sample.a, sample.y, sample.cluster, n_splits=5, estimand="ATT"
    )
    res = estimate_aipw(data, cfg)
    assert res.estimand == "ATT"
    assert res.n_clusters == 150
    assert res.cluster_totals.shape == (150,)
    assert np.isfinite(res.se) and res.se > 0
    # Cluster ATT SE should reflect the design; iid ATT SE on same data is
    # smaller under a high ICC cluster-randomized design.
    data_i = validate_dataset(sample.x, sample.a, sample.y, None, estimand="ATT")
    res_i = estimate_aipw(data_i, cfg)
    assert res.se > res_i.se
