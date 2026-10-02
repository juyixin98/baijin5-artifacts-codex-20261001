"""Contract tests: every rejection maps to a specific failure category."""

from __future__ import annotations

import numpy as np
import pytest

from watershed_backend.config import Settings
from watershed_backend.contracts import (
    SeedPoint,
    build_markers_from_seeds,
    validate_segmentation_input,
)
from watershed_backend.errors import (
    EmptyMarkers,
    ImageTooLarge,
    InvalidConnectivity,
    InvalidLabel,
    NonFiniteGradient,
    SeedConflict,
    SeedOutOfBounds,
    SeedOutsideMask,
    ShapeMismatch,
)

SETTINGS = Settings()
GRADIENT = np.zeros((4, 4), dtype=np.float64)


def test_conflicting_seeds_rejected_with_seed_conflict():
    with pytest.raises(SeedConflict) as excinfo:
        build_markers_from_seeds(
            [SeedPoint(1, 1, 1), SeedPoint(1, 1, 2)], GRADIENT.shape
        )
    assert excinfo.value.category == "SEED_CONFLICT"


def test_duplicate_seed_with_same_label_is_idempotent():
    markers = build_markers_from_seeds(
        [SeedPoint(1, 1, 1), SeedPoint(1, 1, 1)], GRADIENT.shape
    )
    assert markers[1, 1] == 1
    assert (markers > 0).sum() == 1


def test_seed_out_of_bounds_rejected():
    with pytest.raises(SeedOutOfBounds):
        build_markers_from_seeds([SeedPoint(4, 0, 1)], GRADIENT.shape)


def test_negative_seed_label_rejected():
    with pytest.raises(InvalidLabel):
        build_markers_from_seeds([SeedPoint(0, 0, 0)], GRADIENT.shape)


def test_empty_markers_rejected():
    with pytest.raises(EmptyMarkers) as excinfo:
        validate_segmentation_input(
            GRADIENT, np.zeros((4, 4), dtype=np.int32), 8, None, SETTINGS
        )
    assert excinfo.value.category == "EMPTY_MARKERS"


def test_shape_mismatch_rejected():
    with pytest.raises(ShapeMismatch):
        validate_segmentation_input(
            GRADIENT, np.zeros((3, 3), dtype=np.int32), 8, None, SETTINGS
        )


def test_non_finite_gradient_rejected():
    gradient = GRADIENT.copy()
    gradient[0, 0] = np.nan
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[1, 1] = 1
    with pytest.raises(NonFiniteGradient):
        validate_segmentation_input(gradient, markers, 8, None, SETTINGS)


def test_invalid_connectivity_rejected():
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[1, 1] = 1
    with pytest.raises(InvalidConnectivity):
        validate_segmentation_input(GRADIENT, markers, 6, None, SETTINGS)


def test_image_too_large_rejected():
    settings = Settings(max_pixels=8)
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[1, 1] = 1
    with pytest.raises(ImageTooLarge):
        validate_segmentation_input(GRADIENT, markers, 8, None, settings)


def test_seed_on_masked_pixel_rejected():
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[0, 0] = 1
    mask = np.ones((4, 4), dtype=bool)
    mask[0, 0] = False
    with pytest.raises(SeedOutsideMask):
        validate_segmentation_input(GRADIENT, markers, 8, mask, SETTINGS)


def test_mask_shape_mismatch_rejected():
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[1, 1] = 1
    with pytest.raises(ShapeMismatch):
        validate_segmentation_input(
            GRADIENT, markers, 8, np.ones((2, 2), dtype=bool), SETTINGS
        )


def test_valid_input_carries_provenance():
    markers = np.zeros((4, 4), dtype=np.int32)
    markers[1, 1] = 1
    markers[3, 3] = 2
    seg = validate_segmentation_input(GRADIENT, markers, 8, None, SETTINGS)
    assert seg.seed_count == 2
    assert seg.marker_labels == (1, 2)
    assert seg.connectivity == 8
