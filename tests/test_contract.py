"""Contract tests: anchors, halos, digests, spec validation.

Expected values here are hand-computed, not produced by the engine.
"""

from __future__ import annotations

import numpy as np
import pytest

from tileconv.contract import BoundaryMode, KernelSpec, default_anchor, digest_array
from tileconv.errors import InvalidSpecError


class TestDefaultAnchor:
    def test_odd_sizes_anchor_at_center(self):
        assert default_anchor(1) == 0
        assert default_anchor(3) == 1
        assert default_anchor(31) == 15

    def test_even_sizes_anchor_left_of_center(self):
        # even kernels: anchor is the left-of-center sample so the kernel
        # keeps one more sample of right-side context than left-side.
        assert default_anchor(2) == 0
        assert default_anchor(4) == 1
        assert default_anchor(64) == 31

    def test_zero_size_rejected(self):
        with pytest.raises(InvalidSpecError):
            default_anchor(0)


class TestKernelSpecValidation:
    def test_rejects_empty_dense_kernel(self):
        with pytest.raises(InvalidSpecError) as exc:
            KernelSpec.dense([[]])
        assert exc.value.category == "InvalidSpec"

    def test_rejects_1d_dense_kernel(self):
        with pytest.raises(InvalidSpecError):
            KernelSpec.dense([1.0, 2.0, 3.0])

    def test_rejects_non_finite_weights(self):
        with pytest.raises(InvalidSpecError):
            KernelSpec.dense([[1.0, float("nan")], [3.0, 4.0]])

    def test_rejects_out_of_range_anchor(self):
        with pytest.raises(InvalidSpecError):
            KernelSpec.dense([[1.0, 2.0]], anchor=(0, 2))

    def test_rejects_out_of_range_separable_anchor(self):
        with pytest.raises(InvalidSpecError):
            KernelSpec.separable([1.0, 1.0], [1.0, 1.0], col_anchor=5)


class TestHalo:
    def test_odd_kernel_halo_symmetric(self):
        k = KernelSpec.dense(np.ones((5, 3)))
        assert k.halo() == (2, 2, 1, 1)

    def test_even_kernel_halo_keeps_both_sides(self):
        # 4x2 kernel, default anchors (1, 0): convolution form needs
        # n-1-a samples before and a after -> top=2 bottom=1, left=1 right=0.
        # The left/top side must NOT be short-changed.
        k = KernelSpec.dense(np.ones((4, 2)))
        top, bottom, left, right = k.halo()
        assert (top, bottom, left, right) == (2, 1, 1, 0)
        assert top + bottom == 3  # kh - 1
        assert left + right == 1  # kw - 1

    def test_explicit_anchor_shifts_halo(self):
        k = KernelSpec.dense(np.ones((4, 4)), anchor=(3, 0))
        assert k.halo() == (0, 3, 3, 0)

    def test_separable_halo_combines_axes(self):
        k = KernelSpec.separable([1.0, 2.0, 1.0], [1.0, 1.0, 1.0, 1.0])
        # col (axis 0) size 3 anchor 1 -> top/bottom 1; row (axis 1) size 4
        # anchor 1 -> left 2 right 1.
        assert k.halo() == (1, 1, 2, 1)


class TestDigest:
    def test_image_digest_stable_and_shape_sensitive(self):
        a = np.arange(12, dtype=np.float64).reshape(3, 4)
        b = np.arange(12, dtype=np.float64).reshape(3, 4)
        assert digest_array(a) == digest_array(b)
        c = a.reshape(4, 3)
        assert digest_array(a) != digest_array(c)

    def test_image_digest_content_sensitive(self):
        a = np.zeros((4, 4))
        b = np.zeros((4, 4))
        b[2, 3] = 1e-12
        assert digest_array(a) != digest_array(b)

    def test_kernel_digest_sensitive_to_weights_and_anchor(self):
        k1 = KernelSpec.dense([[1.0, 2.0], [3.0, 4.0]])
        k2 = KernelSpec.dense([[1.0, 2.0], [3.0, 4.0]])
        k3 = KernelSpec.dense([[1.0, 2.0], [3.0, 4.000001]])
        k4 = KernelSpec.dense([[1.0, 2.0], [3.0, 4.0]], anchor=(1, 0))
        assert k1.digest() == k2.digest()
        assert k1.digest() != k3.digest()
        assert k1.digest() != k4.digest()

    def test_kernel_roundtrip_serialization(self):
        k = KernelSpec.separable([1.0, 2.0, 1.0], [0.5, 0.5], row_anchor=0)
        k2 = KernelSpec.from_dict(k.to_dict())
        assert k2.digest() == k.digest()
        assert k2.halo() == k.halo()


class TestBoundaryModeValues:
    def test_modes_are_stable_strings(self):
        assert BoundaryMode.MIRROR.value == "mirror"
        assert BoundaryMode.CONSTANT.value == "constant"
        assert BoundaryMode.PERIODIC.value == "periodic"
