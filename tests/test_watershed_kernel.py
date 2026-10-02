"""Kernel tests: hand-computed references, documented tie-breaks, invariants.

Every expected array for the small fixtures is a hand-computed literal from
``tests/fixtures.py`` — not produced by the kernel under test. Larger
fixtures are cross-checked against the independent bucket-flood reference
in ``tests/reference_impl.py``.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from app.core.watershed import (
    UNLABELED,
    WATERSHED_LINE,
    WatershedInputError,
    flood_watershed,
)
from tests import fixtures
from tests.reference_impl import reference_flood

CONN4 = 4
CONN8 = 8


def _structure(connectivity: int) -> np.ndarray:
    if connectivity == 4:
        return np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    return np.ones((3, 3), dtype=int)


# ---------------------------------------------------------------------------
# Hand-computed references
# ---------------------------------------------------------------------------

def test_two_basins_1d_matches_hand_computed():
    result = flood_watershed(
        fixtures.TWO_BASINS_1D_ELEVATION, fixtures.TWO_BASINS_1D_MARKERS, connectivity=CONN4
    )
    np.testing.assert_array_equal(result.labels, fixtures.TWO_BASINS_1D_EXPECTED)
    np.testing.assert_array_equal(result.boundary, fixtures.TWO_BASINS_1D_EXPECTED == WATERSHED_LINE)
    assert result.stats.pixels_watershed == 1


def test_plateau_order_is_deterministic_not_queue_accident():
    """On a flat plateau the ascending flat index decides the flood order."""
    result = flood_watershed(fixtures.PLATEAU_ELEVATION, fixtures.PLATEAU_MARKERS, connectivity=CONN4)
    np.testing.assert_array_equal(result.labels, fixtures.PLATEAU_EXPECTED)
    # Repeating the run must reproduce the identical array bit-for-bit.
    for _ in range(10):
        again = flood_watershed(fixtures.PLATEAU_ELEVATION, fixtures.PLATEAU_MARKERS, connectivity=CONN4)
        np.testing.assert_array_equal(again.labels, fixtures.PLATEAU_EXPECTED)


def test_plateau_2d_matches_hand_computed():
    result = flood_watershed(
        fixtures.PLATEAU_2D_ELEVATION, fixtures.PLATEAU_2D_MARKERS, connectivity=CONN4
    )
    np.testing.assert_array_equal(result.labels, fixtures.PLATEAU_2D_EXPECTED)


def test_corridor_output_is_water_level_propagation_not_nearest_distance():
    """Basin 2 floods a pixel that is Euclidean-closer to seed 1."""
    result = flood_watershed(fixtures.CORRIDOR_ELEVATION, fixtures.CORRIDOR_MARKERS, connectivity=CONN4)
    np.testing.assert_array_equal(result.labels, fixtures.CORRIDOR_EXPECTED)

    r, c = fixtures.CORRIDOR_DISTANCE_CONTRADICTION_PIXEL
    seed1 = np.array([0, 0])
    seed2 = np.array([0, 5])
    pixel = np.array([r, c])
    nearest = 1 if np.linalg.norm(pixel - seed1) < np.linalg.norm(pixel - seed2) else 2
    assert nearest == 1, "fixture broken: pixel should be closer to seed 1"
    assert int(result.labels[r, c]) == 2, "flood must follow the low corridor, not distance"


def test_two_basins_field_matches_hand_computed():
    elevation, markers, expected = fixtures.two_basins()
    result = flood_watershed(elevation, markers, connectivity=CONN4)
    np.testing.assert_array_equal(result.labels, expected)


def test_saddle_matches_hand_computed():
    elevation, markers, expected = fixtures.saddle()
    result = flood_watershed(elevation, markers, connectivity=CONN4)
    np.testing.assert_array_equal(result.labels, expected)
    # The saddle pixel itself is claimed by the lower-index basin.
    assert int(result.labels[2, 2]) == 1


def test_conflicting_adjacent_seeds_are_kept_and_counted():
    result = flood_watershed(fixtures.CONFLICT_ELEVATION, fixtures.CONFLICT_MARKERS, connectivity=CONN4)
    np.testing.assert_array_equal(result.labels, fixtures.CONFLICT_EXPECTED)
    assert result.stats.seed_conflict_count == 2
    assert result.stats.pixels_watershed == 0


def test_seedless_masked_region_stays_unlabeled():
    result = flood_watershed(
        fixtures.MASK_ELEVATION, fixtures.MASK_MARKERS,
        connectivity=CONN4, mask=fixtures.MASK_MASK,
    )
    np.testing.assert_array_equal(result.labels, fixtures.MASK_EXPECTED)
    assert result.stats.pixels_unlabeled == 3


# ---------------------------------------------------------------------------
# Cross-checks against the independent reference implementation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("connectivity", [CONN4, CONN8])
@pytest.mark.parametrize(
    "factory",
    [
        lambda: (fixtures.TWO_BASINS_1D_ELEVATION, fixtures.TWO_BASINS_1D_MARKERS),
        lambda: (fixtures.PLATEAU_2D_ELEVATION, fixtures.PLATEAU_2D_MARKERS),
        lambda: (fixtures.CORRIDOR_ELEVATION, fixtures.CORRIDOR_MARKERS),
        fixtures.two_basins,
        fixtures.saddle,
        fixtures.noisy_gradient,
    ],
    ids=["1d", "plateau2d", "corridor", "two_basins", "saddle", "noisy"],
)
def test_kernel_matches_independent_reference(factory, connectivity):
    built = factory()
    elevation, markers = built[0], built[1]
    result = flood_watershed(elevation, markers, connectivity=connectivity)
    expected = reference_flood(elevation, markers, connectivity=connectivity)
    np.testing.assert_array_equal(result.labels, expected.astype(np.int32))


# ---------------------------------------------------------------------------
# Behavioural invariants
# ---------------------------------------------------------------------------

def test_seed_labels_are_preserved_on_all_standard_fixtures():
    for elevation, markers in (
        (fixtures.TWO_BASINS_1D_ELEVATION, fixtures.TWO_BASINS_1D_MARKERS),
        (fixtures.PLATEAU_2D_ELEVATION, fixtures.PLATEAU_2D_MARKERS),
        (fixtures.CORRIDOR_ELEVATION, fixtures.CORRIDOR_MARKERS),
        fixtures.two_basins()[:2],
        fixtures.saddle()[:2],
        fixtures.noisy_gradient(),
    ):
        for connectivity in (CONN4, CONN8):
            result = flood_watershed(elevation, markers, connectivity=connectivity)
            seed_rows, seed_cols = np.nonzero(markers > 0)
            for r, c in zip(seed_rows, seed_cols):
                assert int(result.labels[r, c]) == int(markers[r, c]), (
                    f"seed at {(r, c)} lost its label under connectivity={connectivity}"
                )


def test_each_basin_is_a_single_connected_component():
    elevation, markers = fixtures.noisy_gradient()
    for connectivity in (CONN4, CONN8):
        result = flood_watershed(elevation, markers, connectivity=connectivity)
        structure = _structure(connectivity)
        for label in (1, 2):
            _, count = ndimage.label(result.labels == label, structure=structure)
            assert count == 1, f"label {label} split into {count} components (conn={connectivity})"


def test_no_adjacent_non_seed_basins_without_ridge():
    """Two different basin labels may never touch without a ridge between them."""
    elevation, markers = fixtures.noisy_gradient()
    result = flood_watershed(elevation, markers, connectivity=CONN4)
    labels = result.labels
    seeds = markers > 0
    for r in range(labels.shape[0]):
        for c in range(labels.shape[1]):
            for rr, cc in ((r, c + 1), (r + 1, c)):
                if rr < labels.shape[0] and cc < labels.shape[1]:
                    la, lb = int(labels[r, c]), int(labels[rr, cc])
                    if la > 0 and lb > 0 and la != lb:
                        assert seeds[r, c] or seeds[rr, cc], (
                            f"adjacent basins {la}/{lb} at {(r, c)}/{(rr, cc)} without ridge"
                        )


def test_result_is_invariant_to_chunk_size():
    """Chunking changes only progress reporting, never the numbers."""
    elevation, markers = fixtures.noisy_gradient()
    baseline = flood_watershed(elevation, markers, connectivity=CONN8).labels
    for chunk_size in (1, 3, 17, 256):
        chunked = flood_watershed(elevation, markers, connectivity=CONN8, chunk_size=chunk_size)
        np.testing.assert_array_equal(chunked.labels, baseline)


def test_progress_callback_reports_monotonic_chunks():
    elevation, markers = fixtures.noisy_gradient()
    events_seen: list[tuple[int, int, int, float]] = []
    flood_watershed(
        elevation, markers, connectivity=CONN8, chunk_size=8,
        on_progress=lambda i, done, total, level: events_seen.append((i, done, total, level)),
    )
    assert events_seen, "progress callback never fired"
    assert events_seen[-1][1] == events_seen[-1][2] == elevation.size
    dones = [e[1] for e in events_seen]
    assert dones == sorted(dones), "processed pixel counts must be non-decreasing"


def test_relabeling_markers_relabels_output_only():
    """Multiplying marker labels permutes the output labels correspondingly."""
    elevation, markers = fixtures.saddle()[:2]
    base = flood_watershed(elevation, markers, connectivity=CONN4).labels
    scaled = flood_watershed(elevation, markers * 10, connectivity=CONN4).labels
    expected = np.where(base > 0, base * 10, base)
    np.testing.assert_array_equal(scaled, expected)


def test_repeated_runs_are_bit_identical_on_noisy_input():
    elevation, markers = fixtures.noisy_gradient()
    first = flood_watershed(elevation, markers, connectivity=CONN8).labels
    for _ in range(5):
        np.testing.assert_array_equal(
            flood_watershed(elevation, markers, connectivity=CONN8).labels, first
        )


def test_connectivity_changes_neighbourhood():
    """4- and 8-connectivity are distinct, fixed definitions."""
    elevation = np.array(
        [
            [1.0, 9.0, 9.0],
            [9.0, 9.0, 9.0],
            [9.0, 9.0, 2.0],
        ]
    )
    markers = np.array([[1, 0, 0], [0, 0, 0], [0, 0, 2]])
    four = flood_watershed(elevation, markers, connectivity=CONN4)
    eight = flood_watershed(elevation, markers, connectivity=CONN8)
    # With 8-connectivity the diagonal link (0,0)-(1,1) lets basin 1 spread
    # one step earlier; the two results must differ somewhere.
    assert not np.array_equal(four.labels, eight.labels)


# ---------------------------------------------------------------------------
# Input contract violations -> categorised errors, never silent success
# ---------------------------------------------------------------------------

def test_no_seeds_raises_categorised_error():
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(np.ones((3, 3)), np.zeros((3, 3), dtype=int))
    assert excinfo.value.category == "no_seeds"


def test_shape_mismatch_raises():
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(np.ones((3, 3)), np.zeros((3, 4), dtype=int))
    assert excinfo.value.category == "shape_mismatch"


def test_non_finite_elevation_raises():
    elevation = np.ones((2, 2))
    elevation[0, 0] = np.nan
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(elevation, np.array([[1, 0], [0, 0]]))
    assert excinfo.value.category == "non_finite_elevation"


def test_negative_marker_raises():
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(np.ones((2, 2)), np.array([[1, -3], [0, 0]]))
    assert excinfo.value.category == "negative_marker"


def test_bad_connectivity_raises():
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(np.ones((2, 2)), np.array([[1, 0], [0, 0]]), connectivity=6)
    assert excinfo.value.category == "bad_connectivity"


def test_seed_outside_mask_raises():
    mask = np.array([[False, True], [True, True]])
    with pytest.raises(WatershedInputError) as excinfo:
        flood_watershed(np.ones((2, 2)), np.array([[1, 0], [0, 0]]), mask=mask)
    assert excinfo.value.category == "seed_outside_mask"


def test_stats_are_consistent_with_output():
    elevation, markers = fixtures.noisy_gradient()
    result = flood_watershed(elevation, markers, connectivity=CONN8)
    stats = result.stats
    assert stats.pixels_total == elevation.size
    assert stats.pixels_labeled == int((result.labels > 0).sum())
    assert stats.pixels_watershed == int((result.labels == WATERSHED_LINE).sum())
    assert stats.pixels_unlabeled == int((result.labels == UNLABELED).sum())
    assert stats.seed_count == int((markers > 0).sum())
    assert stats.label_count == 2
    assert stats.chunks_processed >= 1
    assert "flat index" in stats.tie_break_rule
