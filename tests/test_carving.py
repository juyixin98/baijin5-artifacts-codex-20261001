"""Carving tests: coordinate mapping after removals (acceptance rule 3).

References are hand-derived (hand_4x4 case) or reconstructed independently in
this file (random fixture case): the test rebuilds each intermediate image
from the returned original-coordinate seams and re-checks optimality with
its own brute-force enumerator -- it does not trust the carve loop.
"""

from __future__ import annotations

import numpy as np
import pytest

from seamcarve.carving import CarveState, carve_seams, remove_seam
from seamcarve.config import SeamConfig
from seamcarve.energy import gradient_energy
from seamcarve.errors import ContractViolationError

from .test_kernel import enumerate_all_seams, seam_cost_gradient

HAND_4x4 = np.array(
    [
        [10, 20, 30, 40],
        [10, 20, 30, 40],
        [50, 60, 70, 80],
        [50, 60, 70, 80],
    ],
    dtype=np.uint8,
)


def test_consecutive_removals_update_coordinate_mapping(run_logger):
    """Hand-derived: seam 1 is original column 0; after removal the image
    shifts, and seam 2 (again the leftmost low-energy column) maps back to
    ORIGINAL column 1 -- not column 0 again."""
    config = SeamConfig(energy_mode="gradient", chunk_size=1)
    report = carve_seams(HAND_4x4, np.zeros((4, 4), bool), 2, config)
    first, second = report.seams
    run_logger.emit(
        "assertion", step="carve", basis="hand-derived two-seam carve",
        input="hand_4x4",
        seam1=[list(p) for p in first.points], seam2=[list(p) for p in second.points],
    )
    assert first.points == ((0, 0), (1, 0), (2, 0), (3, 0))
    assert first.energy == pytest.approx(120.0)
    assert second.points == ((0, 1), (1, 1), (2, 1), (3, 1))  # original coords
    assert second.energy == pytest.approx(120.0)
    assert report.final_width == 2


def test_energy_is_recomputed_not_reused(fixture_loader, run_logger):
    """Independently rebuild every intermediate image from the returned
    original-coordinate seams and re-verify optimality at each step with
    a brute-force enumeration over THAT image's fresh energy map."""
    image, _, descriptor = fixture_loader("random_12x10")
    mask = np.zeros(image.shape[:2], bool)
    config = SeamConfig(energy_mode="gradient", chunk_size=2)
    report = carve_seams(image, mask, 4, config)

    current = image.copy()
    col_map = np.broadcast_to(
        np.arange(image.shape[1]), (image.shape[0], image.shape[1])
    ).copy()
    for index, seam in enumerate(report.seams):
        energy = gradient_energy(current)  # fresh energy on current image
        legal = enumerate_all_seams(current.shape[0], current.shape[1],
                                    np.zeros(current.shape[:2], bool))
        brute_min = min(seam_cost_gradient(energy, s) for s in legal)
        run_logger.emit(
            "assertion", step="carve", basis="independent rebuild + brute force",
            input=descriptor["name"], input_sha256=descriptor["sha256"],
            seam_index=index, reported_energy=seam.energy, brute_min=brute_min,
        )
        assert seam.energy == pytest.approx(brute_min)
        # apply the removal described by the ORIGINAL-coordinate seam
        current_cols = []
        for row, (_, orig_col) in enumerate(seam.points):
            hits = np.where(col_map[row] == orig_col)[0]
            assert len(hits) == 1, "original column must map to exactly one current column"
            current_cols.append(int(hits[0]))
        keep = np.ones(current.shape[:2], bool)
        keep[np.arange(current.shape[0]), current_cols] = False
        current = current[keep].reshape(current.shape[0], current.shape[1] - 1)
        col_map = col_map[keep].reshape(col_map.shape[0], col_map.shape[1] - 1)
    assert current.shape[1] == image.shape[1] - 4


def test_removed_pixels_are_distinct_across_seams(fixture_loader):
    """Each (row, original column) pixel is removed at most once."""
    image, _, _ = fixture_loader("random_12x10")
    config = SeamConfig(energy_mode="forward", chunk_size=2)
    report = carve_seams(image, np.zeros(image.shape[:2], bool), 5, config)
    removed = [p for seam in report.seams for p in seam.points]
    assert len(removed) == len(set(removed))
    assert [s.width_after for s in report.seams] == [9, 8, 7, 6, 5]


def test_remove_seam_does_not_mutate_state():
    image = HAND_4x4.copy()
    mask = np.zeros((4, 4), bool)
    state = CarveState.initial(image, mask)
    snapshot = image.copy()
    new_state = remove_seam(state, (0, 0, 0, 0))
    np.testing.assert_array_equal(state.image, snapshot)  # immutability
    assert new_state.width == 3
    np.testing.assert_array_equal(new_state.col_map[0], [1, 2, 3])


def test_carve_rejects_excessive_seam_count():
    config = SeamConfig(min_width=1)
    with pytest.raises(ContractViolationError):
        carve_seams(HAND_4x4, np.zeros((4, 4), bool), 4, config)  # 4 > 4-1


def test_carve_rejects_zero_seams():
    with pytest.raises(ContractViolationError):
        carve_seams(HAND_4x4, np.zeros((4, 4), bool), 0, SeamConfig())
