"""Consecutive seam removal: coordinate mapping and energy recomputation."""

from __future__ import annotations

import numpy as np
import pytest

from app.carving import Carver
from app.errors import InvalidRequestError


class TestConsecutiveRemoval:
    def test_step_image_three_removals(self, step_image, settings):
        """Hand-derived on rows [10,10,10,200,200,200] (zero-energy cols {0,1,4,5}):

        1. seam at original col 0 (energy 0) -> remaining [10,10,200,200,200]
        2. seam at original col 1 (energy 0) -> remaining [10,200,200,200]
        3. recomputed zeros are current cols {2,3} => original col 4
        A carver that kept original coordinates would repeat col 0/1.
        """
        carver = Carver(step_image, None, settings)
        result = carver.remove_many(3)
        assert [r.path_original_cols for r in result.seams] == [
            (0, 0, 0, 0, 0),
            (1, 1, 1, 1, 1),
            (4, 4, 4, 4, 4),
        ]
        assert [r.energy for r in result.seams] == [0.0, 0.0, 0.0]
        assert result.final_image.shape == (5, 3)
        assert result.original_shape == (5, 6)

    def test_energy_recomputed_after_each_removal(self, spike_image, settings):
        """Rows [0,255,0]: first seam removes the spike (col 1, energy 0).

        The remaining image [0,0] is flat, so the *recomputed* energy of the
        second seam is 0.  Reusing the original energy surface would report
        1020 (the stale gradient next to the spike).
        """
        carver = Carver(spike_image, None, settings)
        result = carver.remove_many(2)
        assert result.seams[0].path_original_cols == (1, 1, 1, 1)
        assert result.seams[0].energy == 0.0
        assert result.seams[1].path_original_cols == (0, 0, 0, 0)
        assert result.seams[1].energy == 0.0

    def test_coordinate_map_tracks_columns(self, step_image, settings):
        carver = Carver(step_image, None, settings)
        carver.remove_many(2)
        # Remaining original columns are {2,3,4,5} in every row.
        np.testing.assert_array_equal(
            carver._col_map, np.tile(np.array([2, 3, 4, 5]), (5, 1))
        )

    def test_minimum_width_guard(self, spike_image, settings):
        carver = Carver(spike_image, None, settings)
        with pytest.raises(InvalidRequestError) as excinfo:
            carver.remove_many(3)  # width 3, min_remaining 1 => max 2
        assert excinfo.value.category == "INVALID_REQUEST"

    def test_remove_one_past_minimum_raises(self, spike_image, settings):
        carver = Carver(spike_image, None, settings)
        carver.remove_many(2)  # now width 1
        with pytest.raises(InvalidRequestError):
            carver.remove_one()


class TestProtectedCarving:
    def test_mask_is_carved_with_image(self, step_image, settings):
        """Original col 0 fully protected: seam 1 must take col 1; after
        removal the protection (still on current col 0) keeps blocking."""
        mask = np.zeros((5, 6), dtype=bool)
        mask[:, 0] = True
        carver = Carver(step_image, mask, settings)
        result = carver.remove_many(2)
        assert result.seams[0].path_original_cols == (1, 1, 1, 1, 1)
        # Remaining original cols {0,2,3,4,5}; col 0 still protected, the
        # recomputed zero columns are current {3,4} => original col 4.
        assert result.seams[1].path_original_cols == (4, 4, 4, 4, 4)
        # Protected original col 0 never appears in any removed path.
        assert 0 not in result.seams[0].path_original_cols
        assert 0 not in result.seams[1].path_original_cols

    def test_invalid_mask_shape_rejected(self, step_image, settings):
        with pytest.raises(InvalidRequestError):
            Carver(step_image, np.zeros((5, 5), dtype=bool), settings)

    def test_invalid_image_rejected(self, settings):
        with pytest.raises(InvalidRequestError):
            Carver(np.zeros((0, 3)), None, settings)
