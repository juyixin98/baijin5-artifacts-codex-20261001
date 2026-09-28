"""Statistical data contract: shapes, types, support, positivity.

This module is the single boundary where untrusted array-like input is
validated.  Nothing downstream re-checks these invariants, which keeps the
kernel free of ad-hoc guards while still failing fast at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import InputError, ResourceExhaustedError

_EPS_POSITIVITY = 1e-6


@dataclass(frozen=True)
class Dataset:
    """Validated, row-aligned analysis dataset.

    Attributes
    ----------
    x:
        Feature matrix, shape ``(n, p)``, float64.
    a:
        Binary treatment indicator, shape ``(n,)`` in ``{0, 1}``.
    y:
        Observed outcome, shape ``(n,)``, finite float64.
    cluster:
        Optional integer cluster id per row, shape ``(n,)``; when present the
        independent units are clusters, not rows.
    """

    x: np.ndarray
    a: np.ndarray
    y: np.ndarray
    cluster: np.ndarray | None = None

    @property
    def n(self) -> int:
        return self.x.shape[0]

    @property
    def p(self) -> int:
        return self.x.shape[1]


def _as_1d(name: str, value: object) -> np.ndarray:
    try:
        arr = np.asarray(value)
    except Exception as exc:  # pragma: no cover - numpy raises ValueError
        raise InputError(f"{name} could not be converted to an array") from exc
    if arr.ndim != 1:
        raise InputError(
            f"{name} must be one-dimensional, got shape {arr.shape}"
        )
    return arr


def validate_dataset(
    x: object,
    a: object,
    y: object,
    cluster: object | None = None,
    *,
    max_feature_cells: int = 10_000_000,
    n_splits: int = 5,
    estimand: str = "ATE",
) -> Dataset:
    """Validate raw arrays and return an immutable-ish :class:`Dataset`.

    Checks performed, in order:

    1. shapes / dimensionality and row alignment
    2. dtypes / finiteness (NaN/Inf rejected everywhere)
    3. treatment is exactly binary 0/1 and both arms are present
    4. resource budget ``n * p <= max_feature_cells``
    5. each treatment arm has at least ``n_splits`` units (stratified K-fold
       feasibility; also a coarse positivity requirement)
    6. optional cluster labels: integer-valued.  Two supported designs:

       * individual randomization within clusters: every cluster contains
         both arms (treatment separable from cluster fixed effects);
       * cluster-level randomization: a cluster is homogeneous in A; the
         independent units are clusters and each arm needs at least
         ``n_splits`` clusters for cluster-stratified folding.
    """
    try:
        x_arr = np.asarray(x, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise InputError("x must be a numeric matrix") from exc
    if x_arr.ndim != 2:
        raise InputError(
            f"x must be two-dimensional (n, p), got shape {x_arr.shape}"
        )
    n, p = x_arr.shape
    if n < 2 * n_splits:
        raise InputError(
            f"need at least 2*n_splits={2 * n_splits} rows, got {n}",
            details={"n": n, "n_splits": n_splits},
        )
    if p < 1:
        raise InputError("x must contain at least one feature column")
    if n * p > max_feature_cells:
        raise ResourceExhaustedError(
            f"feature matrix has {n * p} cells, budget is {max_feature_cells}",
            details={"cells": int(n * p), "budget": int(max_feature_cells)},
        )
    if not np.all(np.isfinite(x_arr)):
        raise InputError("x contains NaN or infinite values")

    a_arr = _as_1d("a", a)
    if a_arr.shape[0] != n:
        raise InputError(
            f"a length {a_arr.shape[0]} does not match x rows {n}"
        )
    if not np.all(np.isfinite(a_arr.astype(np.float64, copy=False))):
        raise InputError("a contains NaN or infinite values")
    unique_a = np.unique(a_arr)
    if not (set(np.unique(a_arr).tolist()) <= {0, 1}):
        raise InputError(
            "a must be binary in {0, 1}", details={"observed": unique_a.tolist()}
        )
    a_arr = a_arr.astype(np.int8)

    y_arr = _as_1d("y", y).astype(np.float64, copy=False)
    if y_arr.shape[0] != n:
        raise InputError(
            f"y length {y_arr.shape[0]} does not match x rows {n}"
        )
    if not np.all(np.isfinite(y_arr)):
        raise InputError("y contains NaN or infinite values")

    n_treated = int(np.sum(a_arr == 1))
    n_control = int(n - n_treated)
    if n_treated == 0 or n_control == 0:
        raise InputError(
            "both treatment arms must be present",
            details={"n_treated": n_treated, "n_control": n_control},
        )
    if min(n_treated, n_control) < n_splits:
        raise InputError(
            f"each treatment arm needs >= n_splits={n_splits} units for "
            "stratified cross-fitting",
            details={"n_treated": n_treated, "n_control": n_control},
        )

    if estimand == "ATT" and n_treated < n_splits:  # pragma: no cover
        raise InputError("not enough treated units for ATT cross-fitting")

    cluster_arr: np.ndarray | None = None
    if cluster is not None:
        cluster_arr = _as_1d("cluster", cluster)
        if cluster_arr.shape[0] != n:
            raise InputError(
                f"cluster length {cluster_arr.shape[0]} does not match x rows {n}"
            )
        cluster_float = cluster_arr.astype(np.float64, copy=False)
        if not np.all(np.isfinite(cluster_float)):
            raise InputError("cluster contains NaN or infinite values")
        rounded = np.rint(cluster_float)
        if not np.all(rounded == cluster_float):
            raise InputError("cluster labels must be integer-valued")
        cluster_arr = rounded.astype(np.int64)
        _validate_cluster_design(cluster_arr, a_arr, n_splits)

    return Dataset(x=np.ascontiguousarray(x_arr), a=a_arr, y=y_arr, cluster=cluster_arr)


def _validate_cluster_design(
    cluster: np.ndarray, a: np.ndarray, n_splits: int
) -> None:
    """Validate supported cluster designs.

    Heterogeneous clusters (both arms present) are the individual-
    randomization-within-cluster case.  Homogeneous clusters (one arm each)
    are the cluster-randomized case, which requires at least ``n_splits``
    clusters per arm so every cross-fit fold has treated and control
    clusters.  Mixed / degenerate patterns are rejected.
    """
    ids = np.unique(cluster)
    if ids.shape[0] < 2:
        raise InputError(
            "need at least 2 clusters for variance estimation",
            details={"n_clusters": int(ids.shape[0])},
        )
    treated_set: set[int] = set()
    control_set: set[int] = set()
    mixed = 0
    for g in ids:
        arms = np.unique(a[cluster == g])
        if arms.shape[0] == 2:
            mixed += 1
        elif int(arms[0]) == 1:
            treated_set.add(int(g))
        else:
            control_set.add(int(g))
    homogeneous_present = bool(treated_set or control_set)
    if homogeneous_present and mixed:
        raise InputError(
            "cluster design mixes homogeneous (cluster-randomized) and "
            "mixed clusters; use one design consistently",
            details={
                "homogeneous_clusters": len(treated_set) + len(control_set),
                "mixed_clusters": mixed,
            },
        )
    if homogeneous_present:
        if len(treated_set) < n_splits or len(control_set) < n_splits:
            raise InputError(
                f"cluster-randomized design needs >= n_splits={n_splits} "
                "clusters in each arm",
                details={
                    "treated_clusters": len(treated_set),
                    "control_clusters": len(control_set),
                },
            )
    # Entirely mixed clusters: each cluster contains both arms, but cluster
    # level cross-fitting still needs one cluster per fold at minimum.
    if not homogeneous_present and ids.shape[0] < n_splits:
        raise InputError(
            f"need at least n_splits={n_splits} clusters for cluster folding",
            details={"n_clusters": int(ids.shape[0])},
        )


def check_positivity(pscore: np.ndarray, a: np.ndarray, estimand: str) -> None:
    """Reject estimated propensities at the boundary on the weighted arm.

    ATE weights both arms (1/e on treated, 1/(1-e) on control), so both
    boundaries are checked everywhere.  ATT weights only control rows through
    e/(1-e): a treated unit's propensity never appears in a denominator, but
    a control unit with e -> 1 makes the ATT weight undefined.
    """
    if estimand == "ATT":
        relevant = pscore[a == 0]
        n_bad = int(np.sum(relevant >= 1 - _EPS_POSITIVITY))
        if n_bad:
            from .errors import ComputationError

            raise ComputationError(
                "estimated propensity for a control unit hit 1; ATT weight "
                "e/(1-e) is undefined (positivity violated)",
                details={"n_boundary": n_bad, "threshold": _EPS_POSITIVITY},
            )
        return
    relevant = pscore
    if np.any((relevant <= _EPS_POSITIVITY) | (relevant >= 1 - _EPS_POSITIVITY)):
        from .errors import ComputationError

        n_bad = int(
            np.sum((relevant <= _EPS_POSITIVITY) | (relevant >= 1 - _EPS_POSITIVITY))
        )
        raise ComputationError(
            "estimated propensity hit the [0, 1] boundary; positivity violated",
            details={"n_boundary": n_bad, "threshold": _EPS_POSITIVITY},
        )
