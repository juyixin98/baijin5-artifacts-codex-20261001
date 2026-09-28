"""Contract validation: each rejection is asserted by category."""

from __future__ import annotations

import numpy as np
import pytest

from aipw_backend.contract import validate_dataset
from aipw_backend.errors import InputError, ResourceExhaustedError


def _ok(n: int = 40, seed: int = 0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 2))
    a = (rng.random(n) < 0.5).astype(int)
    y = rng.standard_normal(n)
    return x, a, y


def test_accepts_well_formed_data():
    x, a, y = _ok()
    d = validate_dataset(x, a, y)
    assert d.n == 40 and d.p == 2
    assert d.cluster is None


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda x, a, y: (x[:, 0], a, y), "two-dimensional"),
        (lambda x, a, y: (x, a[:-1], y), "does not match"),
        (lambda x, a, y: (x, a, y[:-1]), "does not match"),
        (lambda x, a, y: (x.astype(float) * np.nan, a, y), "NaN"),
        (lambda x, a, y: (x, a.astype(float) * 0 + 2, y), "binary"),
        (lambda x, a, y: (x, np.zeros_like(a), y), "both treatment arms"),
        (lambda x, a, y: (x, a, y * np.inf), "NaN or infinite"),
    ],
)
def test_input_errors_are_classified(mutate, match):
    x, a, y = _ok()
    with pytest.raises(InputError, match=match):
        validate_dataset(*mutate(x, a, y))


def test_too_few_rows_for_folds_is_input_error():
    x, a, y = _ok(n=6)
    with pytest.raises(InputError, match="2\\*n_splits"):
        validate_dataset(x, a, y, n_splits=5)


def test_cell_budget_is_resource_exhaustion():
    x, a, y = _ok(n=40)
    x = np.random.default_rng(1).standard_normal((40, 1000))
    with pytest.raises(ResourceExhaustedError, match="cells"):
        validate_dataset(x, a, y, max_feature_cells=10_000)
    assert validate_dataset(x, a, y, max_feature_cells=40_000).n == 40


def test_cluster_randomized_design_is_accepted():
    x, a, y = _ok(n=100)
    cluster = np.repeat(np.arange(50), 2)
    a[:] = 0
    # Homogeneous clusters: both rows of even-numbered clusters treated.
    a[cluster % 2 == 0] = 1
    d = validate_dataset(x, a, y, cluster=cluster, n_splits=5)
    assert np.unique(d.cluster).shape[0] == 50


def test_mixed_homogeneous_and_heterogeneous_clusters_rejected():
    x, a, y = _ok(n=100)
    cluster = np.repeat(np.arange(50), 2)
    a[:] = 0
    a[cluster % 2 == 0] = 1  # even clusters homogeneous treated
    a[2] = 1  # cluster 1 (rows 2,3) was control -> now mixed
    with pytest.raises(InputError, match="one design consistently"):
        validate_dataset(x, a, y, cluster=cluster, n_splits=5)


def test_cluster_randomized_needs_enough_clusters_per_arm():
    x, a, y = _ok(n=40)
    cluster = np.repeat(np.arange(20), 2)
    a[:] = 0
    a[cluster < 4] = 1  # only 4 treated clusters < n_splits=5
    with pytest.raises(InputError, match="clusters in each arm"):
        validate_dataset(x, a, y, cluster=cluster, n_splits=5)


def test_heterogeneous_clusters_still_need_one_per_fold():
    x, a, y = _ok(n=40)
    # Only 3 mixed clusters with 10 splits requested -> impossible to fold.
    cluster = np.repeat(np.arange(4), 10)
    a[:] = 0
    a[::2] = 1  # every cluster mixed
    with pytest.raises(InputError, match="clusters for cluster folding"):
        validate_dataset(x, a, y, cluster=cluster, n_splits=5)
    # Same data accepted at n_splits=3.
    d = validate_dataset(x, a, y, cluster=cluster, n_splits=3)
    assert np.unique(d.cluster).shape[0] == 4


def test_non_integer_cluster_labels_rejected():
    x, a, y = _ok()
    cluster = np.repeat(np.arange(20.0), 2) + 0.3
    with pytest.raises(InputError, match="integer-valued"):
        validate_dataset(x, a, y, cluster=cluster)
