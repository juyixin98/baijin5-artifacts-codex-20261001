"""Stretch-rate behavior: speed variation and locally missing segments.

The signal is strictly increasing (``a_i = 0.1 * i``) so equal-value cells
are unique and the optimal alignment is forced onto predictable cells.

Reachability note (hand-derived, shapes these cases): with steps
{(1,1),(2,1),(1,2)} a cell (i, j) is only reachable inside the cone
``i/2 <= j <= 2*i`` — each step advances i+j by 2 or 3 while |i-j| grows
by at most 1. An exactly 2x resampled sequence ends one sample *outside*
the cone, so the slowdown/speedup cases trim one tail sample to land on
the reachable boundary.

Missing-segment optimum (hand-derived): the path must climb from i-j=0 to
i-j=5; each (2,1) step raises i-j by exactly 1, and the cheapest cells at
levels 1..5 cost 0.1, 0.2, 0.2, 0.1, 0.0 (achievable consecutively from
(9,9)), so the optimum is exactly 0.6.
"""

import numpy as np
import pytest

from dtw_service.constraints import SakoeChibaWindow
from dtw_service.dense import dense_dtw
from dtw_service.stretch import smooth_stretch, step_stretches

A = 0.1 * np.arange(40)  # strictly increasing, spacing 0.1
A39 = A[:39]


def test_two_x_slowdown_aligns_each_sample_to_its_copy():
    b = np.repeat(A39, 2)[:-1]  # B at half speed; endpoint (38, 76) on the cone
    res = dense_dtw(A39, b, SakoeChibaWindow(radius=len(A39)))
    assert res.cost == pytest.approx(0.0, abs=1e-9)
    # Every path cell must pair a_i with one of its two copies in B.
    for i, j in res.path:
        assert j in (2 * i, 2 * i + 1)
    rates = step_stretches(res.path)
    # A zero-cost path here can only use (1,1) and (1,2) steps.
    assert set(rates) <= {0.5, 1.0}


def test_two_x_speedup_aligns_every_second_sample():
    b = A39[::2]  # B at double speed; b_j = a_{2j}, endpoint (38, 19)
    res = dense_dtw(A39, b, SakoeChibaWindow(radius=len(A39)))
    assert res.cost == pytest.approx(0.0, abs=1e-9)
    assert res.path == [(2 * k, k) for k in range(20)]
    assert step_stretches(res.path) == [2.0] * 19


def test_local_missing_segment_still_aligns():
    cut_start, cut_len = 12, 5
    b = np.concatenate([A[:cut_start], A[cut_start + cut_len:]])
    res = dense_dtw(A, b, SakoeChibaWindow(radius=cut_len + 1))

    assert res.path is not None
    assert res.path[0] == (0, 0)
    assert res.path[-1] == (len(A) - 1, len(b) - 1)
    # Hand-derived optimum 0.6 (see module docstring).
    assert res.cost == pytest.approx(0.6)
    # After the seam the path re-locks onto j = i - cut_len.
    assert any(j == i - cut_len for i, j in res.path if i >= cut_start + cut_len)
    rates = step_stretches(res.path)
    assert max(rates) == pytest.approx(2.0)  # seam crossed by (2,1) steps
    assert 1.0 in rates  # diagonal steps dominate away from the seam


def test_smooth_stretch_preserves_constant_rate():
    assert smooth_stretch([2.0] * 10, window=5) == pytest.approx([2.0] * 10)


def test_smooth_stretch_empty_input():
    assert smooth_stretch([], window=5) == []


def test_stretch_rejects_non_monotone_path():
    with pytest.raises(ValueError, match="non-monotone"):
        step_stretches([(0, 0), (1, 0)])
