"""Tests for the stride-layout kernel.

These pin down: C/F contiguity (including empty and length-1 axes),
the ported zero-copy reshape decision, broadcasting, overflow and
invalid-stride detection.
"""

from __future__ import annotations

import pytest

from tensorcraft.errors import (
    InvalidStrideError,
    NonContiguousViewError,
    OverflowErrorCore,
    ShapeMismatchError,
    SizeMismatchError,
)
from tensorcraft.tensor import (
    Layout,
    Order,
    broadcast_shape,
    broadcast_strides,
    checked_size,
    infer_unknown_dimension,
    reshape_plan,
    try_nocopy_reshape,
)


class TestContiguity:
    def test_c_contiguous_basic(self):
        layout = Layout((2, 3), (3, 1))
        assert layout.is_c_contiguous()
        assert not layout.is_f_contiguous()

    def test_f_contiguous_basic(self):
        layout = Layout((2, 3), (1, 2))
        assert layout.is_f_contiguous()
        assert not layout.is_c_contiguous()

    def test_transposed_2d_is_f_contiguous(self):
        # The transpose of a C-contiguous (2,3) is (3,2) strides (1,3):
        # F-contiguous, not C-contiguous.
        layout = Layout((3, 2), (1, 3), 0)
        assert not layout.is_c_contiguous()
        assert layout.is_f_contiguous()

    def test_genuinely_non_contiguous_layout(self):
        # (4, 4) window with strides (2, 2) is neither contiguous.
        layout = Layout((4, 4), (2, 2))
        assert not layout.is_c_contiguous()
        assert not layout.is_f_contiguous()

    def test_length_one_axes_ignored(self):
        # A (1, 3, 1, 2) tensor whose non-unit strides are C-like.
        layout = Layout((1, 3, 1, 2), (99, 2, 77, 1))
        assert layout.is_c_contiguous()

    def test_empty_is_both_contiguous(self):
        layout = Layout((3, 0, 5), (0, 5, 1))
        assert layout.is_c_contiguous()
        assert layout.is_f_contiguous()

    def test_scalar_and_1d_are_both(self):
        assert Layout((), ()).is_c_contiguous()
        assert Layout((4,), (1,)).is_c_contiguous()
        assert Layout((4,), (1,)).is_f_contiguous()


class TestRegionBounds:
    def test_positive_strides(self):
        # hi = (2-1)*3 + (3-1)*1 = 5
        assert Layout((2, 3), (3, 1)).region_bounds() == (0, 5)

    def test_negative_stride_with_offset(self):
        # reversed 1-D view starting at offset 5
        layout = Layout((4,), (-1,), storage_offset=5)
        assert layout.region_bounds() == (-3, 0)

    def test_buffer_validation_rejects_out_of_range(self):
        layout = Layout((4,), (1,), storage_offset=8)
        with pytest.raises(InvalidStrideError) as exc:
            layout.validate_for_buffer(10)
        assert exc.value.details["address_max"] == 11

    def test_buffer_validation_negative_start(self):
        layout = Layout((3,), (-1,), storage_offset=0)
        with pytest.raises(InvalidStrideError):
            layout.validate_for_buffer(10)

    def test_empty_layout_imposes_no_region_constraint(self):
        Layout((0,), (1,), storage_offset=1_000).validate_for_buffer(0)


class TestCheckedSize:
    def test_overflow_detected(self):
        with pytest.raises(OverflowErrorCore):
            checked_size((10**20, 10**20))

    def test_zero_factor_after_large_product_is_zero(self):
        # Sequential multiplication: large product then a zero dim -> 0.
        assert checked_size((5, 0, 10**20)) == 0

    def test_negative_dimension_rejected(self):
        with pytest.raises(ShapeMismatchError):
            checked_size((2, -1))


class TestNocopyReshape:
    def test_contiguous_merge_is_view(self):
        strides = try_nocopy_reshape((2, 3), (3, 1), (6,), Order.C)
        assert strides == (1,)

    def test_contiguous_split_is_view(self):
        strides = try_nocopy_reshape((6,), (1,), (2, 3), Order.C)
        assert strides == (3, 1)

    def test_transpose_to_flat_c_requires_copy(self):
        # (3,2) view with strides (1,3) cannot flatten in C without copy.
        assert try_nocopy_reshape(
            (3, 2), (1, 3), (6,), Order.C) is None

    def test_transpose_to_flat_f_is_view(self):
        strides = try_nocopy_reshape((3, 2), (1, 3), (6,), Order.F)
        assert strides == (1,)

    def test_partial_merge_on_transposed_layout_can_stay_view(self):
        # (3,2) strides (1,3) -> (3,2,1): unit axis only.
        strides = try_nocopy_reshape(
            (3, 2), (1, 3), (3, 2, 1), Order.C)
        assert strides == (1, 3, 3)

    def test_matches_numpy_on_random_layouts(self):
        """Property check: our decision agrees with numpy.reshape(copy=False)."""
        import numpy as np
        rng = np.random.default_rng(0)
        for _ in range(200):
            base_shape = tuple(int(x) for x in rng.integers(1, 5, size=3))
            perm = tuple(rng.permutation(3))
            arr = np.arange(int(np.prod(base_shape))).reshape(base_shape)
            view = arr.transpose(perm)
            target_shape = (view.size,)
            ours = try_nocopy_reshape(
                view.shape,
                tuple(s // view.dtype.itemsize for s in view.strides),
                target_shape, Order.C)
            try:
                np.reshape(view, target_shape, order="C", copy=False)
                numpy_view = True
            except ValueError:
                numpy_view = False
            assert (ours is not None) == numpy_view, (
                f"disagree on shape={view.shape} strides={view.strides}")


class TestReshapePlan:
    def test_same_shape_returns_same_layout(self, sample_2x3):
        plan = reshape_plan(sample_2x3.layout, (2, 3), Order.C)
        assert not plan.copied
        assert plan.reason == "shape_unchanged"

    def test_non_contiguous_materializes(self):
        layout = Layout((3, 2), (1, 3))
        plan = reshape_plan(layout, (6,), Order.C)
        assert plan.copied
        assert plan.reason == "non_contiguous_materialized"

    def test_copy_forbidden_raises_typed_error(self):
        layout = Layout((3, 2), (1, 3))
        with pytest.raises(NonContiguousViewError) as exc:
            reshape_plan(layout, (6,), Order.C, copy_allowed=False)
        assert exc.value.details["target"] == (6,)

    def test_empty_reshape_is_view(self):
        plan = reshape_plan(Layout((0,), (1,)), (2, 0, 3), Order.C)
        assert not plan.copied


class TestUnknownDimension:
    def test_infers_single_unknown(self):
        assert infer_unknown_dimension((-1, 3), 12) == (4, 3)

    def test_two_unknowns_rejected(self):
        with pytest.raises(SizeMismatchError) as exc:
            infer_unknown_dimension((-1, -1), 12)
        assert "one unknown" in exc.value.message

    def test_nondivisible_rejected(self):
        with pytest.raises(SizeMismatchError):
            infer_unknown_dimension((5,), 12)

    def test_zero_known_product_rejected(self):
        with pytest.raises(SizeMismatchError):
            infer_unknown_dimension((-1, 0), 12)


class TestBroadcasting:
    def test_broadcast_shape_rules(self):
        assert broadcast_shape((3, 1), (1, 4)) == (3, 4)
        assert broadcast_shape((), (5,)) == (5,)
        assert broadcast_shape((2, 3, 4), (3, 1)) == (2, 3, 4)

    def test_broadcast_shape_mismatch(self):
        from tensorcraft.errors import BroadcastError
        with pytest.raises(BroadcastError):
            broadcast_shape((3,), (4,))

    def test_broadcast_strides_zero_for_new_and_size1(self):
        assert broadcast_strides((3, 1), (4, 0), (2, 3, 4)) == (0, 4, 0)

    def test_broadcast_rank_too_high_rejected(self):
        from tensorcraft.errors import BroadcastError
        with pytest.raises(BroadcastError):
            broadcast_strides((2, 3, 4), (1, 1, 1), (3, 4))
