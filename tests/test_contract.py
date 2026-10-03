"""Contract tests: anchors, halos, boundary index maps, digests.

Expected index sequences below are hand-derived, not produced by the code
under test (cross-checked against numpy.pad semantics in test_kernels.py).
"""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import (
    BoundaryMode,
    ContractError,
    KernelSpec,
    SeparableKernelSpec,
    array_digest,
)
from app.kernels import boundary_indices


class TestAnchorAndHalo:
    def test_odd_kernel_symmetric_halo(self):
        spec = KernelSpec.from_array(np.ones((3, 3)))
        assert spec.anchor == (1, 1)
        assert spec.halo == (1, 1, 1, 1)

    def test_even_kernel_anchor_keeps_extra_left_context(self):
        # k=4, anchor=2 -> offsets -2,-1,0,+1: two samples of left/top
        # context, one of right/bottom.  The halo must not drop either side.
        spec = KernelSpec.from_array(np.ones((4, 4)))
        assert spec.anchor == (2, 2)
        assert spec.halo == (2, 1, 2, 1)

    def test_asymmetric_anchor_halo(self):
        spec = KernelSpec.from_array(np.ones((5, 3)), anchor=(3, 0))
        assert spec.halo == (3, 1, 0, 2)

    def test_anchor_out_of_range_rejected(self):
        with pytest.raises(ContractError):
            KernelSpec.from_array(np.ones((3, 3)), anchor=(3, 0))
        with pytest.raises(ContractError):
            KernelSpec.from_array(np.ones((3, 3)), anchor=(0, -1))

    def test_non_2d_kernel_rejected(self):
        with pytest.raises(ContractError):
            KernelSpec.from_array(np.ones(5))

    def test_separable_halo_matches_dense_outer_product(self):
        sep = SeparableKernelSpec.from_vectors([1.0, 2.0, 3.0, 4.0], [0.5, 0.5])
        dense = sep.to_dense()
        assert sep.halo == dense.halo
        assert np.allclose(dense.array, np.outer([1, 2, 3, 4], [0.5, 0.5]))


class TestBoundaryIndices:
    def test_mirror_sequence_handwritten(self):
        idx, mask = boundary_indices(3, -4, 6, BoundaryMode.MIRROR)
        # period-6 mirror of [0,1,2]: ... 2 2 1 0 | 0 1 2 | 2 1 0 ...
        assert idx.tolist() == [2, 2, 1, 0, 0, 1, 2, 2, 1, 0]
        assert mask is None

    def test_periodic_sequence_handwritten(self):
        idx, mask = boundary_indices(4, -3, 6, BoundaryMode.PERIODIC)
        assert idx.tolist() == [1, 2, 3, 0, 1, 2, 3, 0, 1]
        assert mask is None

    def test_constant_sequence_handwritten(self):
        idx, mask = boundary_indices(3, -2, 5, BoundaryMode.CONSTANT)
        assert mask.tolist() == [True, True, False, False, False, True, True]
        # interior indices are exact; outside positions are clipped placeholders
        assert idx[2:5].tolist() == [0, 1, 2]

    def test_mirror_degenerate_size_one(self):
        idx, _ = boundary_indices(1, -3, 4, BoundaryMode.MIRROR)
        assert idx.tolist() == [0] * 7


class TestDigests:
    def test_array_digest_sensitive_to_single_pixel(self):
        a = np.zeros((4, 4))
        b = a.copy()
        b[2, 3] = 1e-12
        assert array_digest(a) != array_digest(b)

    def test_array_digest_sensitive_to_shape(self):
        a = np.zeros((2, 6))
        b = np.zeros((3, 4))
        assert array_digest(a) != array_digest(b)

    def test_kernel_digest_stable_and_sensitive(self):
        k1 = KernelSpec.from_array(np.ones((3, 3)))
        k2 = KernelSpec.from_array(np.ones((3, 3)))
        k3 = KernelSpec.from_array(np.ones((3, 3)), anchor=(0, 0))
        assert k1.digest() == k2.digest()
        assert k1.digest() != k3.digest()  # same weights, different anchor
