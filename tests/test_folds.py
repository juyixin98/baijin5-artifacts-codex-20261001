"""Fold construction: partitions, stratification and alignment guards."""

from __future__ import annotations

import numpy as np
import pytest

from aipw_backend.errors import InputError
from aipw_backend.folds import check_oo_alignment, make_folds


def test_folds_partition_sample_with_balanced_sizes():
    a = np.array([0] * 53 + [1] * 47)
    split = make_folds(a, n_splits=5, seed=1)
    # every row in exactly one validation fold, once
    all_valid = np.sort(np.concatenate(split.valid_idx))
    assert np.array_equal(all_valid, np.arange(100))
    sizes = [v.size for v in split.valid_idx]
    assert max(sizes) - min(sizes) <= 1
    check_oo_alignment(split)


def test_stratification_puts_both_arms_in_every_fold():
    a = np.array([0] * 50 + [1] * 50)
    split = make_folds(a, n_splits=5, seed=9, stratified=True)
    for v in split.valid_idx:
        assert set(np.unique(a[v]).tolist()) == {0, 1}


def test_assignment_is_seeded_and_reproducible_but_moves_rows():
    a = np.array([0] * 60 + [1] * 60)
    s1 = make_folds(a, 5, seed=123)
    s2 = make_folds(a, 5, seed=123)
    s3 = make_folds(a, 5, seed=124)
    assert np.array_equal(s1.fold_id, s2.fold_id)
    assert not np.array_equal(s1.fold_id, s3.fold_id)


def test_guard_detects_train_validation_overlap():
    a = np.array([0] * 30 + [1] * 30)
    split = make_folds(a, 4, seed=1)
    # Sabotage: leak one training row into fold 0's validation set.
    v0 = split.valid_idx[0]
    bad = np.append(v0, split.train_idx[0][0])
    object.__setattr__(split, "valid_idx", (bad,) + split.valid_idx[1:])
    with pytest.raises(InputError, match="leakage"):
        check_oo_alignment(split)


def test_guard_detects_fold_number_mismatch():
    a = np.array([0] * 30 + [1] * 30)
    split = make_folds(a, 4, seed=1)
    # Sabotage: relabel fold ids without moving index sets. The valid sets no
    # longer agree with the ids, exactly a fold-numbering mismatch.
    wrong_ids = (split.fold_id + 1) % 4
    object.__setattr__(split, "fold_id", wrong_ids)
    with pytest.raises(InputError, match="fold numbering|mismatch"):
        check_oo_alignment(split)


def test_guard_detects_missing_rows_in_validation_union():
    a = np.array([0] * 30 + [1] * 30)
    split = make_folds(a, 4, seed=1)
    v = list(split.valid_idx)
    v[0] = v[0][:-2]  # drop rows entirely
    object.__setattr__(split, "valid_idx", tuple(v))
    with pytest.raises(InputError, match="partition"):
        check_oo_alignment(split)
