"""Tensor-level tests: views, aliasing, overlap writes, reshape decisions.

These assert concrete values *and* memory relations, not just that an
interface is callable.
"""

from __future__ import annotations

import numpy as np
import pytest

from tensorcraft.errors import (
    InvalidStrideError,
    OverlapWriteError,
    ShapeMismatchError,
)
from tensorcraft.tensor import Layout, Storage, Tensor, resolve_dtype


def _window(values=(1, 2, 3, 4, 5), shape=(3, 3), strides=(1, 1)) -> Tensor:
    storage = Storage(np.array(list(values), dtype=np.int64),
                      resolve_dtype("int64"))
    return Tensor(storage, Layout(shape, strides, 0))


class TestTranspose:
    def test_transpose_values_and_alias(self, sample_2x3):
        t = sample_2x3.transpose()
        assert t.shape == (3, 2)
        assert t.strides == (1, 3)
        assert t.to_numpy().tolist() == [[1, 4], [2, 5], [3, 6]]
        assert t.shares_storage_with(sample_2x3)

    def test_transpose_axes_permutation_required(self, sample_2x3):
        with pytest.raises(ShapeMismatchError):
            sample_2x3.transpose([0, 0])
        with pytest.raises(ShapeMismatchError):
            sample_2x3.transpose([0])

    def test_3d_transpose_matches_numpy(self):
        data = np.arange(24).reshape(2, 3, 4)
        t = Tensor.from_nested(data.tolist(), "int64").transpose((2, 0, 1))
        assert t.to_numpy().tolist() == np.transpose(data, (2, 0, 1)).tolist()


class TestReshapeCopyContract:
    def test_contiguous_reshape_is_view(self, sample_2x3):
        out = sample_2x3.reshape((6,), "C")
        assert out.shares_storage_with(sample_2x3)

    def test_transpose_then_reshape_copies(self, sample_2x3):
        transposed = sample_2x3.transpose()
        out = transposed.reshape((6,), "C")
        assert not out.shares_storage_with(transposed)
        # Concrete copied values: C-order materialization of the transpose.
        assert out.to_numpy().tolist() == [1, 4, 2, 5, 3, 6]

    def test_transpose_reshape_values_match_numpy(self):
        data = np.arange(1, 25).reshape(2, 3, 4)
        t = Tensor.from_nested(data.tolist(), "int64")
        out = t.transpose((2, 1, 0)).reshape((4, 6), "C")
        assert out.to_numpy().tolist() == data.transpose(2, 1, 0).reshape(4, 6).tolist()

    def test_allow_copy_false_raises(self, sample_2x3):
        from tensorcraft.errors import NonContiguousViewError
        with pytest.raises(NonContiguousViewError):
            sample_2x3.transpose().reshape((6,), "C", allow_copy=False)

    def test_unknown_dimension(self, sample_2x3):
        out = sample_2x3.reshape((-1, 3), "C")
        assert out.shape == (2, 3)

    def test_reshape_size_mismatch_category(self, sample_2x3):
        from tensorcraft.errors import SizeMismatchError
        with pytest.raises(SizeMismatchError):
            sample_2x3.reshape((7,), "C")


class TestAliasing:
    def test_slice_view_mutates_parent(self):
        base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
        view = base.getitem((slice(None), slice(0, 2)))
        view.assign_scalar(0, policy="reject")
        assert base.to_numpy().tolist() == [[0, 0, 3], [0, 0, 6]]

    def test_materialize_is_independent(self):
        base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
        copy = base.getitem((slice(None), slice(0, 2))).materialize()
        assert not copy.shares_storage_with(base)
        copy.assign_scalar(9, policy="reject")
        assert base.to_numpy().tolist() == [[1, 2, 3], [4, 5, 6]]

    def test_addressed_offsets_detect_alias(self):
        win = _window()
        offsets = win.addressed_offsets()
        # 9 positions, only 5 distinct elements; offset 2 appears 3 times.
        assert len(offsets) == 9
        assert len(set(offsets)) == 5
        assert offsets.count(2) == 3
        assert win.is_self_overlapping()

    def test_overlaps_different_views(self):
        base = Tensor.from_nested(list(range(10)), "int64")
        left = base.getitem(slice(0, 5))
        right = base.getitem(slice(3, 8))
        assert left.overlaps(right)
        copy = left.materialize()
        assert not left.overlaps(copy)


class TestOverlapWrite:
    def test_reject_policy_refuses_and_leaves_buffer_untouched(self):
        win = _window()
        with pytest.raises(OverlapWriteError) as exc:
            win.assign_scalar(7, policy="reject")
        assert exc.value.details["unique_elements"] == 5
        assert exc.value.details["positions"] == 9
        assert win.storage.buffer.tolist() == [1, 2, 3, 4, 5]

    def test_temp_copy_scalar_fill_deterministic(self):
        win = _window()
        win.assign_scalar(7, policy="temp_copy")
        assert win.to_numpy().tolist() == [[7] * 3 for _ in range(3)]
        assert win.storage.buffer.tolist() == [7, 7, 7, 7, 7]

    def test_temp_copy_tensor_assign_last_write_wins(self):
        win = _window()
        source = Tensor.from_nested(
            [[10, 11, 12], [20, 21, 22], [30, 31, 32]], "int64")
        win.assign(source, policy="temp_copy")
        # Derived per C-order scatter, last writer per offset.
        assert win.to_numpy().tolist() == [
            [10, 20, 30], [20, 30, 31], [30, 31, 32]]

    def test_temp_copy_is_stable_across_runs(self):
        source = Tensor.from_nested(
            [[10, 11, 12], [20, 21, 22], [30, 31, 32]], "int64")
        results = []
        for _ in range(10):
            win = _window()
            win.assign(source, policy="temp_copy")
            results.append(win.to_numpy().tolist())
        assert all(result == results[0] for result in results)

    def test_non_overlapping_view_write_needs_no_temp(self):
        base = Tensor.from_nested(list(range(6)), "int64")
        view = base.reshape((2, 3), "C")
        report = view.assign_scalar(1, policy="reject")
        assert not report.temp_copy

    def test_shared_storage_source_is_buffered(self):
        # Source and destination alias; gather-before-scatter must keep the
        # result well-defined and equal to NumPy's explicit-copy behavior.
        base = Tensor.from_nested([[1.0, 2.0], [3.0, 4.0]], "float64")
        dst = base.getitem((slice(None), slice(None)))
        src = base.getitem((slice(None), slice(None)))
        dst.assign(src, policy="reject")  # no overlap: bijective 2x2
        assert dst.to_numpy().tolist() == [[1.0, 2.0], [3.0, 4.0]]


class TestBroadcast:
    def test_zero_strides(self):
        col = Tensor.from_nested([[10], [20], [30]], "int64")
        wide = col.broadcast_to((3, 4))
        assert wide.strides == (1, 0)
        assert wide.shares_storage_with(col)
        assert wide.to_numpy().tolist() == np.broadcast_to(
            np.array([[10], [20], [30]]), (3, 4)).tolist()

    def test_broadcast_arithmetic_outer_sum(self):
        from tensorcraft.tensor import ops
        col = Tensor.from_nested([[10], [20], [30]], "int64")
        row = Tensor.from_nested([[1, 2, 3, 4]], "int64")
        wide_col = col.broadcast_to((3, 4))
        wide_row = row.broadcast_to((3, 4))
        result = ops.elementwise(wide_col, wide_row, "add").tensor
        expected = (np.array([[10], [20], [30]])
                    + np.array([[1, 2, 3, 4]])).tolist()
        assert result.to_numpy().tolist() == expected
        # Result is fresh storage distinct from both operands.
        assert not result.shares_storage_with(col)
        assert not result.shares_storage_with(row)


class TestEmptyTensors:
    def test_empty_construction_and_views(self):
        e = Tensor.from_nested([[], [], []], "float64")
        assert e.shape == (3, 0)
        assert e.transpose().shape == (0, 3)
        assert e.reshape((0,), "C").shape == (0,)
        assert e.to_numpy().tolist() == [[], [], []]

    def test_empty_arithmetic_and_reduce(self):
        from tensorcraft.tensor import ops
        e = Tensor.from_nested(np.zeros((3, 0)).tolist(), "float64")
        assert ops.unary(e, "neg").tensor.to_numpy().shape == (3, 0)
        total = ops.reduce_sum(e).tensor
        assert total.to_numpy().reshape(()).item() == 0.0

    def test_empty_reshape_with_unknown(self):
        e = Tensor.from_nested([], "float64")
        out = e.reshape((-1, 2), "C")
        assert out.shape == (0, 2)

    def test_zero_dimensional_scalar_preserved(self):
        scalar = Tensor.from_nested(3.0, "float64")
        assert scalar.shape == ()
        assert scalar.strides == ()
        assert scalar.to_numpy().reshape(()).item() == 3.0
        from tensorcraft.tensor import ops
        negated = ops.unary(scalar, "neg").tensor
        assert negated.shape == ()
        assert negated.to_numpy().reshape(()).item() == -3.0


class TestStorageBounds:
    def test_layout_beyond_buffer_rejected_at_construction(self):
        storage = Storage(np.zeros(5, dtype=np.int64), resolve_dtype("int64"))
        with pytest.raises(InvalidStrideError):
            Tensor(storage, Layout((3,), (1,), storage_offset=4))

    def test_negative_stride_without_room_rejected(self):
        storage = Storage(np.zeros(5, dtype=np.int64), resolve_dtype("int64"))
        with pytest.raises(InvalidStrideError):
            Tensor(storage, Layout((6,), (-1,), storage_offset=5))
