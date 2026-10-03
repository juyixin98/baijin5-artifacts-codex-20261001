"""DP-kernel tests: displacement limit, ties, protection, brute-force oracle."""

from __future__ import annotations

import numpy as np
import pytest

from app.errors import InvalidRequestError, MaskShapeError, NoLegalSeamError
from app.kernel import find_seam_forward, find_seam_gradient
from tests.reference import (
    brute_force_forward,
    brute_force_gradient,
    path_cost_forward,
    path_cost_gradient,
)

HIGH = 1000.0


class TestDisplacementLimit:
    """The adjacent-row displacement limit is explicit and enforced."""

    # Low-energy cells form the zig-zag 3 -> 4 -> 2; everything else is HIGH.
    #   row0: low only col 3
    #   row1: low only col 4
    #   row2: low only col 2
    ENERGY = np.array(
        [
            [HIGH, HIGH, HIGH, 0.0, HIGH],
            [HIGH, HIGH, HIGH, HIGH, 0.0],
            [HIGH, HIGH, 0.0, HIGH, HIGH],
        ]
    )

    def test_displacement_1_cannot_reach_zigzag(self):
        result = find_seam_gradient(self.ENERGY, max_displacement=1)
        # 3 -> 4 is legal, but 4 -> 2 is a jump of 2: one HIGH cell is forced.
        assert result.energy == HIGH

    def test_displacement_2_follows_zigzag(self):
        result = find_seam_gradient(self.ENERGY, max_displacement=2)
        assert result.energy == 0.0
        assert result.path == (3, 4, 2)

    def test_displacement_must_be_positive(self):
        with pytest.raises(InvalidRequestError) as excinfo:
            find_seam_gradient(self.ENERGY, max_displacement=0)
        assert excinfo.value.category == "INVALID_REQUEST"


class TestTieBreaking:
    def test_flat_image_picks_leftmost_column(self, settings):
        energy = np.zeros((4, 4))
        result = find_seam_gradient(energy, max_displacement=1)
        assert result.path == (0, 0, 0, 0)
        assert result.energy == 0.0
        assert result.final_row_ties == 4

    def test_tied_valleys_pick_leftmost(self, settings):
        # Zero-energy columns {0, 3}: two optimal straight seams, col 0 wins.
        energy = np.tile(np.array([[0.0, 5.0, 5.0, 0.0]]), (4, 1))
        result = find_seam_gradient(energy, max_displacement=1)
        assert result.path == (0, 0, 0, 0)
        assert result.final_row_ties == 2


class TestProtection:
    def test_full_row_protection_rejected(self):
        energy = np.zeros((5, 6))
        mask = np.zeros((5, 6), dtype=bool)
        mask[2, :] = True
        with pytest.raises(NoLegalSeamError) as excinfo:
            find_seam_gradient(energy, mask, max_displacement=1)
        assert excinfo.value.category == "NO_LEGAL_SEAM"
        assert excinfo.value.details["blocked_at_row"] == 2

    def test_fully_protected_image_rejected(self):
        energy = np.zeros((3, 3))
        mask = np.ones((3, 3), dtype=bool)
        with pytest.raises(NoLegalSeamError):
            find_seam_gradient(energy, mask, max_displacement=1)
        with pytest.raises(NoLegalSeamError):
            find_seam_forward(np.zeros((3, 3)), mask)

    def test_forward_full_row_protection_rejected(self):
        lum = np.zeros((4, 4))
        mask = np.zeros((4, 4), dtype=bool)
        mask[1, :] = True
        with pytest.raises(NoLegalSeamError):
            find_seam_forward(lum, mask)

    def test_forward_protected_start_column_not_entered(self):
        # Regression: protected pixels in row 0 must be banned even though
        # no transition "enters" them (seeded into the initial DP row).
        lum = np.zeros((3, 3))
        mask = np.zeros((3, 3), dtype=bool)
        mask[0, 0] = True  # the otherwise-cheapest straight start
        result = find_seam_forward(lum, mask)
        assert result.path[0] != 0
        for row, col in enumerate(result.path):
            assert not mask[row, col]

    def test_mask_shape_mismatch(self):
        with pytest.raises(MaskShapeError) as excinfo:
            find_seam_gradient(np.zeros((3, 3)), np.zeros((3, 4), dtype=bool))
        assert excinfo.value.category == "MASK_SHAPE_MISMATCH"

    @pytest.mark.parametrize("seed", range(10))
    def test_seam_never_crosses_protected(self, seed):
        rng = np.random.default_rng(seed)
        energy = rng.integers(0, 50, size=(5, 6)).astype(np.float64)
        mask = rng.random((5, 6)) < 0.4
        mask[:, 0] = False  # keep column 0 free so a legal seam exists
        result = find_seam_gradient(energy, mask, max_displacement=1)
        for row, col in enumerate(result.path):
            assert not mask[row, col], f"seam crosses protected pixel {(row, col)}"


class TestBruteForceOracle:
    """Small images: DP result must match exhaustive path enumeration."""

    @pytest.mark.parametrize("seed", range(8))
    @pytest.mark.parametrize("displacement", [1, 2])
    def test_gradient_matches_exhaustive(self, seed, displacement):
        rng = np.random.default_rng(1000 + seed)
        energy = rng.integers(0, 50, size=(5, 5)).astype(np.float64)
        mask = rng.random((5, 5)) < 0.3
        mask[:, 0] = False
        oracle = brute_force_gradient(energy, mask, displacement)
        assert oracle is not None
        best_cost, _ = oracle
        result = find_seam_gradient(energy, mask, displacement)
        assert result.energy == pytest.approx(best_cost, abs=1e-9)
        # The DP path itself must be feasible and optimal.
        for row, col in enumerate(result.path):
            assert not mask[row, col]
        for row in range(1, len(result.path)):
            assert abs(result.path[row] - result.path[row - 1]) <= displacement
        assert path_cost_gradient(energy, list(result.path)) == pytest.approx(
            best_cost, abs=1e-9
        )

    @pytest.mark.parametrize("seed", range(8))
    def test_forward_matches_exhaustive(self, seed):
        rng = np.random.default_rng(2000 + seed)
        lum = rng.integers(0, 20, size=(4, 5)).astype(np.float64)
        mask = rng.random((4, 5)) < 0.3
        mask[:, 0] = False
        oracle = brute_force_forward(lum, mask)
        assert oracle is not None
        best_cost, _ = oracle
        result = find_seam_forward(lum, mask)
        assert result.energy == pytest.approx(best_cost, abs=1e-9)
        assert path_cost_forward(lum, list(result.path)) == pytest.approx(
            best_cost, abs=1e-9
        )

    def test_forward_hand_computed_optimum(self):
        # Hand-derived cumulative row 1 = [9, 0, 9]; optimum seam is [1, 1].
        lum = np.array([[0.0, 0.0, 0.0], [9.0, 0.0, 9.0]])
        result = find_seam_forward(lum)
        assert result.path == (1, 1)
        assert result.energy == 0.0
