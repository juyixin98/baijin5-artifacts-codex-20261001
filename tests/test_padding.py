"""Boundary semantics tests.

All expected arrays in this file are hand-computed from the documented index
maps (docs/boundary-semantics.md) — they are not produced by the code under
test. Cross-checks against np.pad use numpy's independent implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from tileconv.contract import BoundaryMode
from tileconv.errors import InvalidSpecError
from tileconv.padding import boundary_indices, extract_window


class TestBoundaryIndicesHandComputed:
    def test_periodic_indices(self):
        idx = np.arange(-5, 8)
        got = boundary_indices(idx, 4, BoundaryMode.PERIODIC)
        # hand-computed: i mod 4
        expected = np.array([3, 0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3])
        np.testing.assert_array_equal(got, expected)

    def test_mirror_indices_edge_not_repeated(self):
        idx = np.arange(-5, 8)
        got = boundary_indices(idx, 4, BoundaryMode.MIRROR)
        # whole-sample symmetric, period 2n-2 = 6:
        # -5->1, -4->2, -3->3, -2->2, -1->1, 0..3, 4->2, 5->1, 6->0, 7->1
        expected = np.array([1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1])
        np.testing.assert_array_equal(got, expected)

    def test_mirror_single_element_axis(self):
        got = boundary_indices(np.arange(-3, 4), 1, BoundaryMode.MIRROR)
        np.testing.assert_array_equal(got, np.zeros(7, dtype=int))

    def test_constant_mode_has_no_indices(self):
        with pytest.raises(InvalidSpecError):
            boundary_indices(np.array([-1, 0]), 4, BoundaryMode.CONSTANT)


class TestBoundaryIndicesVsNumpyPad:
    """np.pad is an independent implementation of the same semantics."""

    def test_mirror_matches_np_pad_reflect(self):
        arr = np.arange(7, dtype=np.float64)
        padded = np.pad(arr, (5, 5), mode="reflect")
        idx = boundary_indices(np.arange(-5, 12), 7, BoundaryMode.MIRROR)
        np.testing.assert_array_equal(arr[idx], padded)

    def test_periodic_matches_np_pad_wrap(self):
        arr = np.arange(7, dtype=np.float64)
        padded = np.pad(arr, (5, 5), mode="wrap")
        idx = boundary_indices(np.arange(-5, 12), 7, BoundaryMode.PERIODIC)
        np.testing.assert_array_equal(arr[idx], padded)


class TestExtractWindow:
    def test_interior_window_is_plain_slice(self):
        img = np.arange(20, dtype=np.float64).reshape(4, 5)
        win = extract_window(img, 1, 3, 2, 5, BoundaryMode.MIRROR)
        np.testing.assert_array_equal(win, img[1:3, 2:5])

    def test_mirror_window_hand_computed(self):
        img = np.array([[10.0, 20.0, 30.0]])
        # window cols -2..4 over width 3: idx -2->2, -1->1, 0,1,2, 3->1
        win = extract_window(img, 0, 1, -2, 4, BoundaryMode.MIRROR)
        np.testing.assert_array_equal(win, [[30.0, 20.0, 10.0, 20.0, 30.0, 20.0]])

    def test_periodic_window_hand_computed(self):
        img = np.array([[1.0, 2.0, 3.0]])
        # cols -1..5 over width 3: idx -1->2, 0,1,2, 3->0, 4->1
        win = extract_window(img, 0, 1, -1, 5, BoundaryMode.PERIODIC)
        np.testing.assert_array_equal(win, [[3.0, 1.0, 2.0, 3.0, 1.0, 2.0]])

    def test_constant_window_hand_computed(self):
        img = np.array([[1.0, 2.0], [3.0, 4.0]])
        win = extract_window(img, -1, 3, -2, 3, BoundaryMode.CONSTANT, cval=9.0)
        expected = np.array([
            [9.0, 9.0, 9.0, 9.0, 9.0],
            [9.0, 9.0, 1.0, 2.0, 9.0],
            [9.0, 9.0, 3.0, 4.0, 9.0],
            [9.0, 9.0, 9.0, 9.0, 9.0],
        ])
        np.testing.assert_array_equal(win, expected)

    def test_halo_larger_than_image_mirror(self):
        # reflection must fold repeatedly, not clamp
        img = np.array([[0.0, 1.0, 2.0]])
        win = extract_window(img, 0, 1, -5, 3, BoundaryMode.MIRROR)
        # idx -5..2 over n=3, period 4: -5->1, -4->0, -3->1, -2->2, -1->1, 0,1,2
        np.testing.assert_array_equal(
            win, [[1.0, 0.0, 1.0, 2.0, 1.0, 0.0, 1.0, 2.0]])

    def test_halo_larger_than_image_periodic(self):
        img = np.array([[0.0, 1.0, 2.0]])
        win = extract_window(img, 0, 1, -4, 3, BoundaryMode.PERIODIC)
        # idx -4..2 mod 3: 2,0,1,2,0,1,2
        np.testing.assert_array_equal(
            win, [[2.0, 0.0, 1.0, 2.0, 0.0, 1.0, 2.0]])

    def test_window_uses_far_edge_for_periodic_not_crop_interior(self):
        # Regression guard: periodic context for a left-edge window must come
        # from the image's right edge, not from inside the clipped crop.
        img = np.arange(10, dtype=np.float64).reshape(1, 10)
        win = extract_window(img, 0, 1, -3, 2, BoundaryMode.PERIODIC)
        np.testing.assert_array_equal(win, [[7.0, 8.0, 9.0, 0.0, 1.0]])

    def test_empty_window_rejected(self):
        img = np.zeros((3, 3))
        with pytest.raises(InvalidSpecError):
            extract_window(img, 2, 2, 0, 3, BoundaryMode.MIRROR)
