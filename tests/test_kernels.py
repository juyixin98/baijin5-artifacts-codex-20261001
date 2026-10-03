"""Kernel tests: impulse responses, boundary semantics, independent references.

References are independent of the code under test:
- ``naive_reference`` (app.reference): literal loop over the contract formula
- ``scipy_reference`` (app.reference): scipy.ndimage
- hand-computed arrays written literally in this file
"""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import BoundaryMode, KernelSpec
from app.fixtures import (
    impulse_image,
    separable_gaussian,
    tagged_border_image,
)
from app.kernels import (
    extract_region,
    filter_direct,
    filter_separable_direct,
)
from app.reference import naive_reference, scipy_reference

ALL_MODES = [BoundaryMode.MIRROR, BoundaryMode.CONSTANT, BoundaryMode.PERIODIC]


class TestImpulseResponse:
    def test_odd_kernel_impulse_places_kernel_at_anchor(self):
        # 3x3 kernel, anchor (1,1): impulse at (2,2).  The operator is
        # correlation-form, so the response is the kernel mirrored about
        # the anchor, scaled by the impulse value (hand-computed below).
        img = impulse_image((5, 5), pos=(2, 2), value=2.0)
        k = KernelSpec.from_array([[1.0, 2.0, 3.0],
                                   [4.0, 5.0, 6.0],
                                   [7.0, 8.0, 9.0]])
        out = filter_direct(img, k, BoundaryMode.CONSTANT, cval=0.0)
        expected = np.zeros((5, 5))
        expected[1:4, 1:4] = 2.0 * np.array([[9.0, 8.0, 7.0],
                                             [6.0, 5.0, 4.0],
                                             [3.0, 2.0, 1.0]])
        np.testing.assert_array_equal(out, expected)

    def test_even_kernel_impulse_offsets(self):
        # k=2, anchor (1,1) -> offsets (-1,-1)..(0,0): the response sits
        # at and down-right of the impulse, mirrored about the anchor.
        # Hand-computed: out[2,2]=w[1,1], out[2,3]=w[1,0],
        #                out[3,2]=w[0,1], out[3,3]=w[0,0].
        img = impulse_image((4, 4), pos=(2, 2))
        k = KernelSpec.from_array([[1.0, 2.0], [3.0, 4.0]], anchor=(1, 1))
        out = filter_direct(img, k, BoundaryMode.CONSTANT, cval=0.0)
        expected = np.array([
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 4, 3],
            [0, 0, 2, 1],
        ], dtype=np.float64)
        np.testing.assert_array_equal(out, expected)


class TestBoundarySemantics:
    def test_constant_boundary_uses_cval(self):
        # 2x2 image of ones, 3x3 all-ones kernel, cval=10:
        # out[0,0] sees 4 image samples and 5 constants -> 4 + 50 = 54.
        img = np.ones((2, 2))
        k = KernelSpec.from_array(np.ones((3, 3)))
        out = filter_direct(img, k, BoundaryMode.CONSTANT, cval=10.0)
        assert out[0, 0] == pytest.approx(4.0 + 5 * 10.0)
        assert out[1, 1] == pytest.approx(4.0 + 5 * 10.0)
        assert out[0, 1] == pytest.approx(4.0 + 5 * 10.0)

    def test_mirror_reflects_tagged_border(self):
        # Top border tagged 1.0: a window reaching 2 above row 0 must read
        # mirrored samples (edge repeated: ext[-1]=row0, ext[-2]=row1),
        # not a constant.
        img = tagged_border_image((8, 8), border=1,
                                  border_values=(1.0, 2.0, 3.0, 4.0))
        region = extract_region(img, -2, 1, 3, 4, BoundaryMode.MIRROR)
        np.testing.assert_array_equal(region[0], img[1, 3:4])  # ext[-2]=row1
        np.testing.assert_array_equal(region[1], img[0, 3:4])  # ext[-1]=row0
        np.testing.assert_array_equal(region[2], img[0, 3:4])
        assert region[1, 0] == 1.0  # mirrored top border keeps its tag

    def test_periodic_wraps_to_far_edge(self):
        img = tagged_border_image((8, 8), border=1,
                                  border_values=(1.0, 2.0, 3.0, 4.0))
        region = extract_region(img, 0, 1, 6, 10, BoundaryMode.PERIODIC)
        # cols 6,7 then wrap to 0,1: right border tag 4.0 then left tag 3.0.
        assert region[0, 1] == 4.0  # last column
        assert region[0, 2] == 3.0  # wraps to first column

    def test_halo_larger_than_image_periodic(self):
        # Kernel support (halo 4) exceeds image width 3: periodic extension
        # must still wrap the *whole* image, not just a clipped local tile.
        img = np.array([[1.0, 2.0, 3.0]])
        region = extract_region(img, 0, 1, -4, 6, BoundaryMode.PERIODIC)
        # pos -4..5 mod 3 -> idx 2,0,1,2,0,1,2,0,1,2
        np.testing.assert_array_equal(
            region[0], [3, 1, 2, 3, 1, 2, 3, 1, 2, 3]
        )


class TestAgainstIndependentReferences:
    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("kshape,anchor", [
        ((3, 3), None),
        ((4, 4), None),       # even, default anchor (2,2)
        ((4, 2), (1, 0)),     # even x odd, asymmetric anchor
        ((5, 3), (3, 0)),     # odd, off-centre anchor
    ])
    def test_direct_matches_naive_and_scipy(self, mode, kshape, anchor):
        rng = np.random.default_rng(42)
        img = rng.standard_normal((11, 9))
        k = KernelSpec.from_array(rng.standard_normal(kshape), anchor=anchor)
        out = filter_direct(img, k, mode, cval=0.5)
        np.testing.assert_allclose(
            out, naive_reference(img, k, mode, cval=0.5), atol=1e-12
        )
        np.testing.assert_allclose(
            out, scipy_reference(img, k, mode, cval=0.5), atol=1e-10
        )

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_separable_matches_dense_outer_product(self, mode):
        rng = np.random.default_rng(7)
        img = rng.standard_normal((13, 10))
        sep = separable_gaussian(5, 1.3)
        dense = sep.to_dense()
        out_sep = filter_separable_direct(img, sep, mode)
        out_dense = filter_direct(img, dense, mode)
        np.testing.assert_allclose(out_sep, out_dense, atol=1e-12)
        np.testing.assert_allclose(
            out_sep, scipy_reference(img, sep, mode), atol=1e-10
        )

    def test_separable_even_length_matches_dense(self):
        rng = np.random.default_rng(8)
        img = rng.standard_normal((9, 9))
        from app.contract import SeparableKernelSpec

        sep = SeparableKernelSpec.from_vectors([0.2, 0.3, 0.3, 0.2],
                                               [0.5, 0.5])
        out_sep = filter_separable_direct(img, sep, BoundaryMode.MIRROR)
        out_dense = filter_direct(img, sep.to_dense(), BoundaryMode.MIRROR)
        np.testing.assert_allclose(out_sep, out_dense, atol=1e-12)
