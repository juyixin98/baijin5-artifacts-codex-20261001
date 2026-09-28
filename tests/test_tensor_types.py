"""Layer-1 tests: sparse tensor validation and typed failure categories."""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)


def _batch(indices, values, **kw):
    kw.setdefault("vocab_size", 8)
    kw.setdefault("expected_dim", 4)
    return SparseGradientBatch.from_lists(indices, values, **kw)


def test_valid_batch_is_copied_and_immutable_to_caller():
    idx = np.array([1, 2])
    val = np.ones((2, 4))
    b = _batch(idx, val)
    assert b.nnz == 2 and b.dim == 4
    idx[0] = 999
    val[0, 0] = 999.0
    # Retained caller arrays cannot mutate validated state.
    assert b.indices[0] == 1
    assert b.values[0, 0] == 1.0


def test_default_scale_is_token_count():
    b = _batch([0, 0, 1], np.ones((3, 4)))
    assert b.scale == 3.0
    assert not b.is_empty


def test_empty_batch_is_a_valid_non_error_state():
    b = _batch([], np.empty((0, 4)))
    assert b.is_empty and b.nnz == 0
    assert b.scale == 0.0 == float(b.nnz)


def test_out_of_range_index_rejects_whole_batch_with_category():
    idx = [1, 8, 2]  # 8 is illegal for vocab_size=8; 1 and 2 are valid
    val = np.ones((3, 4))
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch(idx, val)
    assert exc.value.category is ErrorCategory.INDEX_OUT_OF_RANGE
    assert exc.value.details["bad_index"] == 8
    assert exc.value.details["bad_position"] == 1
    assert exc.value.details["n_bad"] == 1


def test_negative_index_is_out_of_range_not_validation():
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0, -1], np.ones((2, 4)))
    assert exc.value.category is ErrorCategory.INDEX_OUT_OF_RANGE


def test_non_finite_values_are_numeric_errors():
    val = np.array([[1.0, 2.0, 3.0, np.nan], [0.0] * 4])
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0, 1], val)
    assert exc.value.category is ErrorCategory.NUMERIC_ERROR
    assert exc.value.details["non_finite_entries"] >= 1


def test_shape_mismatch_is_validation_error():
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0, 1, 2], np.ones((2, 4)))
    assert exc.value.category is ErrorCategory.VALIDATION_ERROR
    assert "differ" in exc.value.message


def test_wrong_dim_is_validation_error():
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0], np.ones((1, 3)))
    assert exc.value.category is ErrorCategory.VALIDATION_ERROR


def test_non_integer_index_is_rejected():
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0.5, 1.0], np.ones((2, 4)))
    assert exc.value.category is ErrorCategory.VALIDATION_ERROR


def test_integral_float_indices_are_accepted():
    b = _batch([2.0, 3.0], np.ones((2, 4)))
    assert b.indices.dtype == np.int64
    assert b.indices.tolist() == [2, 3]


@pytest.mark.parametrize("bad_scale", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_scale_categories(bad_scale):
    with pytest.raises(SparseEmbeddingError) as exc:
        _batch([0], np.ones((1, 4)), scale=bad_scale)
    assert exc.value.category is ErrorCategory.VALIDATION_ERROR


def test_error_to_dict_is_stable_and_serializable():
    try:
        _batch([8], np.ones((1, 4)))
    except SparseEmbeddingError as exc:
        d = exc.to_dict()
    assert d["category"] == "index_out_of_range"
    assert isinstance(d["message"], str) and d["details"]["bad_index"] == 8
