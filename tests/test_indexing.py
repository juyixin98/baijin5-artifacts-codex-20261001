"""Tests for indexer parsing (ints, slices, Ellipsis, newaxis)."""

from __future__ import annotations

import pytest

from tensorcraft.errors import (
    IndexOutOfBoundsError,
    InvalidIndexError,
    ShapeMismatchError,
)
from tensorcraft.tensor import Tensor, parse_indexer


class TestParseBasic:
    def test_integer_removes_axis_and_applies_offset(self):
        plan = parse_indexer(1, (2, 3), (3, 1))
        assert plan.shape == (3,)
        assert plan.strides == (1,)
        assert plan.storage_offset == 3

    def test_negative_integer_normalized(self):
        plan = parse_indexer(-1, (2, 3), (3, 1))
        assert plan.storage_offset == 3

    def test_slice_basic(self):
        plan = parse_indexer(slice(1, 4, 2), (6,), (1,))
        assert plan.shape == (2,)
        assert plan.strides == (2,)
        assert plan.storage_offset == 1

    def test_reverse_slice_negative_stride(self):
        plan = parse_indexer(slice(None, None, -1), (4,), (1,))
        assert plan.shape == (4,)
        assert plan.strides == (-1,)
        assert plan.storage_offset == 3

    def test_reverse_with_start_stop(self):
        # [4:1:-1] -> elements 4,3,2 ; offset starts at 4
        plan = parse_indexer(slice(4, 1, -1), (6,), (1,))
        assert plan.shape == (3,)
        assert plan.strides == (-1,)
        assert plan.storage_offset == 4

    def test_newaxis_inserts_zero_stride(self):
        plan = parse_indexer((None, slice(None)), (3,), (1,))
        assert plan.shape == (1, 3)
        assert plan.strides == (0, 1)

    def test_ellipsis_expands_full_slices(self):
        plan = parse_indexer((Ellipsis, 0), (2, 3, 4), (12, 4, 1))
        assert plan.shape == (2, 3)
        assert plan.strides == (12, 4)
        assert plan.storage_offset == 0

    def test_trailing_axes_implicit(self):
        plan = parse_indexer(0, (2, 3, 4), (12, 4, 1))
        assert plan.shape == (3, 4)
        assert plan.strides == (4, 1)


class TestParseErrors:
    def test_integer_out_of_bounds(self):
        with pytest.raises(IndexOutOfBoundsError) as exc:
            parse_indexer(5, (2, 3), (3, 1))
        assert exc.value.details["axis"] == 0
        assert exc.value.details["size"] == 2

    def test_negative_integer_out_of_bounds(self):
        with pytest.raises(IndexOutOfBoundsError):
            parse_indexer(-9, (2,), (1,))

    def test_zero_step_rejected(self):
        with pytest.raises(InvalidIndexError) as exc:
            parse_indexer(slice(0, 2, 0), (4,), (1,))
        assert exc.value.category == "INVALID_INDEX"

    def test_float_index_rejected(self):
        with pytest.raises(InvalidIndexError):
            parse_indexer(1.5, (4,), (1,))

    def test_boolean_index_rejected(self):
        with pytest.raises(InvalidIndexError):
            parse_indexer(True, (4,), (1,))

    def test_too_many_indices(self):
        with pytest.raises(ShapeMismatchError):
            parse_indexer((0, 0, 0), (2, 2), (2, 1))

    def test_double_ellipsis_rejected(self):
        with pytest.raises(InvalidIndexError):
            parse_indexer((Ellipsis, Ellipsis), (2, 2), (2, 1))

    def test_list_index_rejected(self):
        with pytest.raises(InvalidIndexError):
            parse_indexer([0, 1], (4,), (1,))


class TestOnTensor:
    def test_slice_values_match_numpy(self):
        import numpy as np
        data = np.arange(24).reshape(2, 3, 4)
        tensor = Tensor.from_nested(data.tolist(), "int64")
        out = tensor.getitem((slice(None), slice(None, None, -1)))
        expected = data[:, ::-1]
        assert out.to_numpy().tolist() == expected.tolist()
        assert out.strides == tuple(s // data.itemsize for s in expected.strides)

    def test_reverse_view_is_alias(self, sample_2x3):
        view = sample_2x3.getitem((slice(None, None, -1),))
        assert view.shares_storage_with(sample_2x3)

    def test_empty_slice(self):
        tensor = Tensor.from_nested([[1, 2], [3, 4]], "int64")
        view = tensor.getitem((slice(0, 0), slice(None)))
        assert view.shape == (0, 2)
        assert view.size == 0
