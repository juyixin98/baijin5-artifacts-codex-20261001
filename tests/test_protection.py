"""Protected-region constraint tests (acceptance rule 2)."""

from __future__ import annotations

import numpy as np
import pytest

from seamcarve.errors import NoLegalSeamError
from seamcarve.kernel import find_seam

HAND_4x4 = np.array(
    [
        [10, 20, 30, 40],
        [10, 20, 30, 40],
        [50, 60, 70, 80],
        [50, 60, 70, 80],
    ],
    dtype=np.uint8,
)


@pytest.mark.parametrize("mode", ["gradient", "forward"])
def test_fully_protected_row_rejects(mode, fixture_loader, run_logger):
    """A fully protected row makes every vertical seam illegal -> reject."""
    image, mask, descriptor = fixture_loader("protected_row_6x6")
    assert mask is not None and mask[2, :].all()
    run_logger.emit(
        "assertion", step="dp", basis="full protected row -> +inf cost row",
        input=descriptor["name"], input_sha256=descriptor["sha256"], mode=mode,
        expected_category="NO_LEGAL_SEAM",
    )
    with pytest.raises(NoLegalSeamError):
        find_seam(image, mask, mode)


@pytest.mark.parametrize("mode", ["gradient", "forward"])
def test_fully_protected_image_rejects(mode):
    mask = np.ones((4, 4), bool)
    with pytest.raises(NoLegalSeamError):
        find_seam(HAND_4x4, mask, mode)


def test_protected_column_forces_detour_gradient(run_logger):
    """Hand-derived: protecting (1,0) and (2,0) moves the min seam from
    column 0 to column 3, energy 120 (hand-recomputed DP)."""
    mask = np.zeros((4, 4), bool)
    mask[1, 0] = True
    mask[2, 0] = True
    result = find_seam(HAND_4x4, mask, "gradient")
    run_logger.emit(
        "assertion", step="dp", basis="hand-derived DP with protected cells",
        input="hand_4x4", protected=[[1, 0], [2, 0]],
        expected_columns=[3, 3, 3, 3], expected_energy=120.0,
        got_columns=list(result.columns), got_energy=result.energy,
    )
    assert result.columns == (3, 3, 3, 3)
    assert result.energy == pytest.approx(120.0)
    assert not any(mask[i, c] for i, c in enumerate(result.columns))


@pytest.mark.parametrize("mode", ["gradient", "forward"])
@pytest.mark.parametrize("seed", range(5))
def test_seam_never_crosses_protected_cells(mode, seed):
    rng = np.random.default_rng(300 + seed)
    image = rng.integers(0, 256, size=(6, 6), dtype=np.uint8)
    mask = np.zeros((6, 6), bool)
    # protect scattered cells but keep at least one full legal corridor
    protected = rng.random((6, 6)) < 0.3
    protected[:, 0] = False
    mask |= protected
    result = find_seam(image, mask, mode)
    assert not any(mask[i, c] for i, c in enumerate(result.columns))
