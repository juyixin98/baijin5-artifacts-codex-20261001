"""Deterministic stratified K-fold assignment for cross-fitting.

Fold ids are an integer vector ``fold[i] in {0, ..., K-1}`` aligned 1:1 with
dataset rows.  Sample ``i`` is predicted by a model trained on the rows with
``fold != fold[i]``.  Assignment uses a seeded permutation **separately within
each treatment arm**, so every fold contains both arms (stratification) and
the mapping is reproducible but does not depend on row order alone.

The construction here deliberately mirrors scikit-learn's StratifiedKFold
shuffle semantics conceptually but is implemented independently with
numpy.random default_rng, so the test oracle does not share code with the
estimator.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import InputError


@dataclass(frozen=True)
class FoldSplit:
    """Fold assignment plus, per fold, explicit train/validate index sets."""

    fold_id: np.ndarray  # (n,) int, fold of each *row*
    train_idx: tuple[np.ndarray, ...]  # train_idx[k]: rows that TRAIN fold k
    valid_idx: tuple[np.ndarray, ...]  # valid_idx[k]: rows PREDICTED in fold k

    @property
    def n_splits(self) -> int:
        return len(self.valid_idx)


def make_folds(
    a: np.ndarray,
    n_splits: int,
    seed: int,
    stratified: bool = True,
    cluster: np.ndarray | None = None,
) -> FoldSplit:
    """Build fold assignment.

    Row-level (``cluster=None``): stratified assigns folds with a seeded
    greedy deal that balances both overall fold sizes and per-arm counts;
    non-stratified deals one seeded permutation round-robin.

    Cluster-level (``cluster`` given): a whole cluster always lands in one
    fold, so held-out clusters never leak their common shock into training.
    Clusters are dealt greedily to minimize the deviation of each fold's
    treated/control *row* counts from their targets, which keeps every fold
    populated with both arms under both individual and cluster randomization.
    """
    n = a.shape[0]
    fold_id = np.empty(n, dtype=np.int64)
    rng = np.random.default_rng(seed)

    if cluster is not None:
        _assign_cluster_folds(cluster, a, n_splits, rng, fold_id)
        _assert_partition(fold_id, n, n_splits, a, cluster_level=True)
    else:
        groups: list[np.ndarray]
        if stratified:
            groups = [np.flatnonzero(np.asarray(a) == arm) for arm in (1, 0)]
        else:
            groups = [np.arange(n)]

        if not stratified:
            # Single arm: plain seeded round-robin deal.
            for idx in groups:
                perm = idx[rng.permutation(idx.shape[0])]
                fold_id[perm] = np.arange(perm.shape[0], dtype=np.int64) % n_splits
        else:
            # Stratified greedy deal: permute within each arm, then assign each
            # row to the currently smallest fold (ties -> lowest fold index).
            # Balancing on running totals keeps BOTH per-arm counts and overall
            # fold sizes within one of each other, unlike two independent
            # round-robins whose remainders pile onto the same folds.
            totals = np.zeros(n_splits, dtype=np.int64)
            for idx in groups:
                perm = idx[rng.permutation(idx.shape[0])]
                for row in perm:
                    k = int(np.argmin(totals))
                    fold_id[row] = k
                    totals[k] += 1
        _assert_partition(fold_id, n, n_splits, a if stratified else None)

    train_idx: list[np.ndarray] = []
    valid_idx: list[np.ndarray] = []
    for k in range(n_splits):
        valid = np.flatnonzero(fold_id == k)
        train = np.flatnonzero(fold_id != k)
        # Sort for deterministic downstream iteration; alignment is by index,
        # never by position, so sorting cannot scramble correspondence.
        valid_idx.append(np.sort(valid))
        train_idx.append(np.sort(train))
    return FoldSplit(
        fold_id=fold_id,
        train_idx=tuple(train_idx),
        valid_idx=tuple(valid_idx),
    )


def _assign_cluster_folds(
    cluster: np.ndarray,
    a: np.ndarray,
    n_splits: int,
    rng: np.random.Generator,
    fold_id: np.ndarray,
) -> None:
    """Assign whole clusters to folds.

    Homogeneous clusters (cluster-randomized design) are dealt separately by
    arm, greedily minimizing that arm's running row total, so every fold
    receives treated and control clusters.  Heterogeneous clusters
    (individual randomization within clusters) are dealt greedily on total row
    counts; both arms per fold then follow from within-cluster mixing.
    """
    ids = np.unique(cluster)
    members = [np.flatnonzero(cluster == g) for g in ids]
    homogeneous = bool(
        all(np.unique(a[mem]).shape[0] == 1 for mem in members)
    )
    if homogeneous:
        treated = [m for m in members if a[m][0] == 1]
        control = [m for m in members if a[m][0] == 0]
        totals_1 = np.zeros(n_splits, dtype=np.int64)
        totals_0 = np.zeros(n_splits, dtype=np.int64)
        for arm_members, totals in ((treated, totals_1), (control, totals_0)):
            order = rng.permutation(len(arm_members))
            for j in order:
                m = arm_members[int(j)]
                k = int(np.argmin(totals))
                fold_id[m] = k
                totals[k] += m.shape[0]
    else:
        totals = np.zeros(n_splits, dtype=np.int64)
        for j in rng.permutation(len(members)):
            m = members[int(j)]
            k = int(np.argmin(totals))
            fold_id[m] = k
            totals[k] += m.shape[0]


def _assert_partition(
    fold_id: np.ndarray,
    n: int,
    n_splits: int,
    a: np.ndarray | None,
    *,
    cluster_level: bool = False,
) -> None:
    if fold_id.shape[0] != n:
        raise InputError("fold assignment length mismatch")
    if set(np.unique(fold_id).tolist()) != set(range(n_splits)):
        raise InputError("every fold must be non-empty and ids must be 0..K-1")
    if not cluster_level:
        counts = np.bincount(fold_id, minlength=n_splits)
        if np.max(counts) - np.min(counts) > 1:
            raise InputError("fold sizes are unbalanced by more than one row")
    if a is not None:
        for arm in (0, 1):
            arm_folds = fold_id[a == arm]
            present = np.bincount(arm_folds, minlength=n_splits)
            if np.any(present == 0):
                raise InputError("each fold must contain both treatment arms")


def check_oo_alignment(split: FoldSplit) -> None:
    """Structural guard against train/validate leakage and fold mislabelling.

        * train[k] and valid[k] are disjoint and their union is all rows
        * valid folds are mutually exclusive and jointly exhaustive
        * valid[k] is exactly the set of rows whose fold id equals k

    Tests additionally call this after any fold manipulation to prove the
    correspondence used by out-of-fold prediction.
    """
    n = split.fold_id.shape[0]
    # 1. leakage: train and validation within a fold must be disjoint.
    for k, (tr, va) in enumerate(zip(split.train_idx, split.valid_idx)):
        if np.intersect1d(tr, va, assume_unique=False).size != 0:
            raise InputError(f"train/validate leakage in fold {k}")
    # 2. coverage: validation folds are mutually exclusive and exhaustive.
    all_valid = np.concatenate(split.valid_idx)
    if all_valid.shape[0] != n or np.any(np.sort(all_valid) != np.arange(n)):
        raise InputError("validation folds must partition the sample")
    # 3. numbering: each valid set is exactly the rows carrying its fold id.
    for k, (tr, va) in enumerate(zip(split.train_idx, split.valid_idx)):
        expected = np.flatnonzero(split.fold_id == k)
        if not np.array_equal(np.sort(va), np.sort(expected)):
            raise InputError(f"fold {k} validation id mismatch (fold numbering)")
        if tr.shape[0] + va.shape[0] != n:
            raise InputError(f"fold {k} train+valid do not cover all rows")
