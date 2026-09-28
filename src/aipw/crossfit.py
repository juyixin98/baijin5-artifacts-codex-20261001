"""Cross-fitting: fold assignment and out-of-fold (OOF) prediction alignment.

Contract enforced here
----------------------
* ``make_stratified_folds`` returns a fold-id array indexed EXACTLY like the
  data rows; every row appears in exactly one validation fold (ids 0..folds-1).
* ``cross_fit`` fits every nuisance model on the training rows of a fold and
  predicts only that fold's validation rows. The returned ``OOFPredictions``
  arrays are row-aligned with the input data: ``mu0[i]`` is the prediction for
  row ``i`` from a model that NEVER saw row ``i`` during fitting.
* A check asserts the validation folds form an exact partition; any off-by-one
  or duplicated fold id raises ``StateConflictError`` rather than silently
  misaligning predictions.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contract import Config, Dataset, FoldDiagnostics, InputError, StateConflictError
from .models import LogisticRegression, OLSRidge


def make_stratified_folds(dataset: Dataset, k: int,
                          rng: np.random.Generator) -> np.ndarray:
    """Treatment-stratified K-fold assignment.

    Within each treatment stratum, rows are shuffled with ``rng`` and dealt
    round-robin to folds. Fold sizes differ by at most one and every fold
    contains both treatment levels whenever the stratum sizes permit.
    """
    if k < 2:
        raise InputError("need at least 2 folds")
    n = dataset.n
    if n < 2 * k:
        raise InputError(
            "not enough rows for the requested folds",
            details={"n": n, "folds": k, "minimum_rows": 2 * k},
        )
    fold_id = np.full(n, -1, dtype=np.int64)
    for members in (np.flatnonzero(dataset.a == 0), np.flatnonzero(dataset.a == 1)):
        perm = members[rng.permutation(members.size)]
        for j, row in enumerate(perm):
            fold_id[int(row)] = j % k
    _validate_partition(fold_id, k, n)
    return fold_id


def make_cluster_folds(dataset: Dataset, k: int,
                       rng: np.random.Generator) -> np.ndarray:
    """Fold assignment at the CLUSTER level (required with clustered data).

    Every member of a cluster lands in the same fold, so no validation row's
    cluster-mates were seen during training. Clusters are dealt round-robin
    after a shuffle, stratified approximately by the cluster-level treatment
    (constant within a cluster).
    """
    if dataset.clusters is None:
        raise InputError("cluster folds requested but dataset has no cluster_id")
    unique_clusters, inverse = np.unique(dataset.clusters, return_inverse=True)
    g = unique_clusters.size
    if g < 2 * k:
        raise InputError(
            "not enough clusters for the requested folds",
            details={"clusters": g, "folds": k, "minimum_clusters": 2 * k},
        )
    # cluster-level treatment; stratify only when constant within each cluster
    cluster_a = np.full(g, -1.0)
    mixed = False
    for cg in range(g):
        members = np.flatnonzero(inverse == cg)
        vals = np.unique(dataset.a[members])
        if vals.size == 1:
            cluster_a[cg] = vals[0]
        else:
            mixed = True
    cluster_fold = np.full(g, -1, dtype=np.int64)
    if mixed:
        # assignment varies within clusters: unstratified cluster folds
        perm = rng.permutation(g)
        for j, cg in enumerate(perm):
            cluster_fold[int(cg)] = j % k
    else:
        for stratum in (np.flatnonzero(cluster_a == 0),
                        np.flatnonzero(cluster_a == 1)):
            perm = stratum[rng.permutation(stratum.size)]
            for j, cg in enumerate(perm):
                cluster_fold[int(cg)] = j % k
    if np.any(cluster_fold < 0):
        raise InputError("some clusters received no fold id")
    fold_id = cluster_fold[inverse]
    _validate_partition(fold_id, k, dataset.n)
    return fold_id


def _validate_partition(fold_id: np.ndarray, k: int, n: int) -> None:
    if fold_id.shape != (n,):
        raise StateConflictError(
            "fold id array is not row-aligned with the data",
            details={"fold_shape": tuple(fold_id.shape), "n": n},
        )
    if np.any(fold_id < 0) or np.any(fold_id >= k):
        raise StateConflictError(
            "fold ids out of range or unassigned rows",
            details={"min": int(fold_id.min()), "max": int(fold_id.max()), "k": k},
        )
    counts = np.bincount(fold_id, minlength=k)
    if int(counts.sum()) != n or np.any(counts == 0):
        raise StateConflictError(
            "folds do not form an exact partition",
            details={"counts": counts.tolist(), "n": n},
        )


@dataclass(frozen=True)
class FoldScalerStats:
    """Train-only standardization statistics retained for the leakage audit."""
    fold: int
    mean: np.ndarray
    scale: np.ndarray


@dataclass(frozen=True)
class OOFPredictions:
    fold_id: np.ndarray          # (n,)
    propensity: np.ndarray       # e_hat for each row, from a model not trained on it
    mu0: np.ndarray              # m_hat(X, 0)
    mu1: np.ndarray              # m_hat(X, 1)
    diagnostics: tuple[FoldDiagnostics, ...]
    # per-fold train-only scaler stats for the control (m0) and treated (m1)
    # outcome models; retained so an audit can prove stats came from train rows
    scalers0: tuple[FoldScalerStats, ...]
    scalers1: tuple[FoldScalerStats, ...]

    def assert_alignment(self, n: int) -> None:
        for name, arr in (("propensity", self.propensity),
                          ("mu0", self.mu0), ("mu1", self.mu1)):
            if arr.shape != (n,):
                raise StateConflictError(
                    f"OOF array {name!r} is misaligned with samples",
                    details={"shape": tuple(arr.shape), "expected": (n,)},
                )
            if np.any(~np.isfinite(arr)):
                raise StateConflictError(
                    f"OOF array {name!r} contains non-finite predictions"
                )
        if np.any((self.propensity <= 0.0) | (self.propensity >= 1.0)):
            raise StateConflictError("propensity outside the open (0,1) interval")


def cross_fit(dataset: Dataset, config: Config, fold_id: np.ndarray) -> OOFPredictions:
    """Fit K propensity + 2K outcome models; return row-aligned OOF predictions."""
    _validate_partition(fold_id, config.folds, dataset.n)
    n = dataset.n
    ps = np.empty(n)
    mu0 = np.empty(n)
    mu1 = np.empty(n)
    diagnostics: list[FoldDiagnostics] = []
    scalers0: list[FoldScalerStats] = []
    scalers1: list[FoldScalerStats] = []

    for f in range(config.folds):
        valid = fold_id == f
        train = ~valid
        x_tr, x_va = dataset.x[train], dataset.x[valid]
        a_tr = dataset.a[train]
        y_tr = dataset.y[train]

        treated = a_tr == 1
        control = ~treated
        if treated.sum() == 0 or control.sum() == 0:
            # Positivity within a training fold. Fatal for this fold split.
            raise StateConflictError(
                "a training fold contains only one treatment level "
                "(positivity violation); use fewer folds or a different seed",
                details={"fold": f, "treated": int(treated.sum()),
                         "control": int(control.sum())},
            )

        # --- propensity, fit on train rows only -----------------------------
        prop_model = LogisticRegression(
            l2_penalty=config.propensity_model.l2_penalty,
            max_iter=config.propensity_model.max_iter,
            tol=config.propensity_model.tol,
        ).fit(x_tr, a_tr)
        ps[valid] = prop_model.predict_proba(x_va)

        # --- two outcome models; standardization stats from TRAIN rows only -
        out_cfg = config.outcome_models
        m0 = OLSRidge(intercept=out_cfg.intercept,
                      standardize=out_cfg.standardize).fit(x_tr[control], y_tr[control])
        m1 = OLSRidge(intercept=out_cfg.intercept,
                      standardize=out_cfg.standardize).fit(x_tr[treated], y_tr[treated])
        mu0[valid] = m0.predict(x_va)
        mu1[valid] = m1.predict(x_va)

        # Leakage audit quantity: the standardized TRAINING design is centered
        # at ~0 by construction. Diagnostics compare this against validation
        # rows transformed with the SAME (train) statistics.
        train_centered = (m1.scaler_.transform(x_tr)
                          if m1.scaler_ is not None else x_tr)

        def _stats(model):
            if model.scaler_ is not None:
                return (np.asarray(model.scaler_.mean_, dtype=float).copy(),
                        np.asarray(model.scaler_.scale_, dtype=float).copy())
            # standardization disabled: record the identity stats so the audit
            # contract (one entry per fold/arm) still holds
            p = x_tr.shape[1]
            return np.zeros(p), np.ones(p)

        mean0, scale0 = _stats(m0)
        mean1, scale1 = _stats(m1)
        scalers0.append(FoldScalerStats(fold=f, mean=mean0, scale=scale0))
        scalers1.append(FoldScalerStats(fold=f, mean=mean1, scale=scale1))
        diagnostics.append(FoldDiagnostics(
            fold=f,
            train_size=int(train.sum()),
            valid_size=int(valid.sum()),
            treated_train=int(treated.sum()),
            control_train=int(control.sum()),
            prop_min=float(ps[valid].min()),
            prop_max=float(ps[valid].max()),
            outcome0_train_rss=m0.training_residual_sum_squares(x_tr[control],
                                                                y_tr[control]),
            outcome1_train_rss=m1.training_residual_sum_squares(x_tr[treated],
                                                                y_tr[treated]),
            train_x_mean_abs_max=float(np.abs(train_centered.mean(axis=0)).max()),
        ))

    oof = OOFPredictions(fold_id=fold_id, propensity=ps, mu0=mu0, mu1=mu1,
                         diagnostics=tuple(diagnostics),
                         scalers0=tuple(scalers0), scalers1=tuple(scalers1))
    oof.assert_alignment(n)
    return oof
