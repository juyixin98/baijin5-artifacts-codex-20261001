"""Kernel correctness against hand-computed expectations.

The expected arrays come from tests/fixtures.py and were derived by
hand from the definition of geodesic reconstruction — never by running
the kernels — so these tests are an independent check of both engines.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.kernel import reconstruct_queue, reconstruct_reference
from app.schemas import Connectivity
from tests.fixtures import ALL_FIXTURES


@pytest.mark.parametrize("fixture_name", sorted(ALL_FIXTURES))
@pytest.mark.parametrize("connectivity", [Connectivity.FOUR, Connectivity.EIGHT])
def test_reference_matches_hand_computed(
    fixture_name, connectivity, convergence_log
):
    marker, mask, expected = ALL_FIXTURES[fixture_name]()
    result, stats = reconstruct_reference(marker, mask, connectivity)
    convergence_log.record(
        test="reference_vs_hand",
        fixture=fixture_name,
        connectivity=int(connectivity),
        iterations=stats.iterations,
        changed_pixels=stats.changed_pixels,
    )
    np.testing.assert_array_equal(result, expected)
    assert stats.iterations is not None and stats.iterations >= 1


@pytest.mark.parametrize("fixture_name", sorted(ALL_FIXTURES))
@pytest.mark.parametrize("connectivity", [Connectivity.FOUR, Connectivity.EIGHT])
def test_queue_matches_hand_computed(fixture_name, connectivity, convergence_log):
    marker, mask, expected = ALL_FIXTURES[fixture_name]()
    result, stats = reconstruct_queue(marker, mask, connectivity)
    convergence_log.record(
        test="queue_vs_hand",
        fixture=fixture_name,
        connectivity=int(connectivity),
        queue_pops=stats.queue_pops,
        changed_pixels=stats.changed_pixels,
    )
    np.testing.assert_array_equal(result, expected)


def test_connectivity_changes_result():
    """4- vs 8-connectivity must differ on a diagonal-only bridge."""
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[0, 0] = mask[1, 1] = mask[2, 2] = mask[3, 3] = 200
    marker = np.zeros_like(mask)
    marker[0, 0] = 100

    result4, _ = reconstruct_queue(marker, mask, Connectivity.FOUR)
    result8, _ = reconstruct_queue(marker, mask, Connectivity.EIGHT)

    # 4-connected: the diagonal is unreachable, only the seed survives.
    expected4 = np.zeros_like(mask)
    expected4[0, 0] = 100
    np.testing.assert_array_equal(result4, expected4)
    # 8-connected: the flood walks the diagonal.
    assert result8[3, 3] == 100
