"""Kernel tests with hand-computed reference skeletons.

Every expected skeleton below was derived by hand from the Zhang-Suen
rules, not by running the implementation:
- 3x3 solid block: sub-iteration 1 deletes the 4 corners plus the east
  and south edge midpoints (6 pixels); sub-iteration 2 deletes the
  remaining 2 edge midpoints, leaving exactly the centre pixel.
- 3x3 plus: each arm tip has B=3 (two diagonal neighbours plus the
  centre) and A=1, so sub-iteration 1 deletes all 4 arms, leaving the
  centre pixel.
- 1x5 line: interior pixels have A=2, endpoints have B=1 -> fixed point.
- 2x4 bar: sub-iteration 1 deletes the whole bottom row plus the two
  top corners (6 pixels), leaving a 2-pixel stub at {(0,1),(0,2)}.
"""

from __future__ import annotations

import numpy as np

from app.kernel import deletion_mask, thin
from app.samples import block_3x3, line_1x5, plus_3x3, ring, thin_bridge
from app.validation import count_components, count_endpoints, count_holes


def _coords(image: np.ndarray) -> set[tuple[int, int]]:
    return {tuple(p) for p in np.argwhere(image)}


def test_block_3x3_thins_to_centre_with_exact_round_trace() -> None:
    result = thin(block_3x3())
    assert result.converged
    assert result.rounds == 2
    # round 1: sub1 deletes 6 boundary pixels, sub2 deletes 2; round 2 clean.
    assert result.deletions_per_round == ((6, 2), (0, 0))
    assert _coords(result.skeleton) == {(1, 1)}


def test_line_1x5_is_fixed_point() -> None:
    result = thin(line_1x5())
    assert result.deletions_per_round == ((0, 0),)
    np.testing.assert_array_equal(result.skeleton, line_1x5())


def test_plus_3x3_thins_to_centre() -> None:
    result = thin(plus_3x3())
    assert result.deletions_per_round == ((4, 0), (0, 0))
    assert _coords(result.skeleton) == {(1, 1)}


def test_bar_2x4_leaves_two_pixel_stub() -> None:
    bar = np.ones((2, 4), dtype=np.uint8)
    result = thin(bar)
    assert result.deletions_per_round == ((6, 0), (0, 0))
    assert _coords(result.skeleton) == {(0, 1), (0, 2)}


def test_deletion_mask_uses_single_prior_state() -> None:
    """All four 2x2 pixels are deletable in the SAME sub-iteration.

    Sequential in-place deletion would change the neighbourhood of the
    later pixels; the parallel mask must flag all four at once.
    """
    block = np.ones((2, 2), dtype=np.uint8)
    mask = deletion_mask(block, subiteration=1)
    assert int(mask.sum()) == 4


def test_ring_preserves_hole_and_component() -> None:
    image = ring()
    assert count_components(image) == 1
    assert count_holes(image) == 1
    assert count_endpoints(image) == 0
    result = thin(image)
    assert result.converged
    assert count_components(result.skeleton) == 1
    assert count_holes(result.skeleton) == 1
    assert count_endpoints(result.skeleton) == 0


def test_thin_bridge_stays_connected() -> None:
    image = thin_bridge()
    assert count_components(image) == 1
    result = thin(image)
    assert result.converged
    assert count_components(result.skeleton) == 1
    assert count_holes(result.skeleton) == 0
    assert 0 < int(result.skeleton.sum()) < int(image.sum())


def test_max_rounds_reports_non_convergence() -> None:
    result = thin(ring(), max_rounds=1)
    assert not result.converged
    assert result.rounds == 1
