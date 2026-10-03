"""Energy-function tests with hand-derived expected values."""

from __future__ import annotations

import numpy as np
import pytest

from app.energy import forward_costs, gradient_energy, to_luminance


class TestGradientEnergy:
    def test_constant_image_has_zero_energy(self):
        energy = gradient_energy(np.full((3, 3), 128))
        np.testing.assert_array_equal(energy, np.zeros((3, 3)))

    def test_step_columns_hand_computed(self):
        # Constant rows [0, 0, 255]; Sobel gx = 4*(right-left) with reflect
        # borders, gy = 0.  Hand-derived: columns 1 and 2 see the step.
        image = np.tile(np.array([[0, 0, 255]]), (3, 1))
        energy = gradient_energy(image)
        expected = np.tile(np.array([[0.0, 1020.0, 1020.0]]), (3, 1))
        np.testing.assert_allclose(energy, expected, rtol=0, atol=1e-12)

    def test_rgb_uses_luminance(self):
        gray = np.tile(np.array([[0, 0, 255]]), (3, 1))
        rgb = np.stack([gray, gray, gray], axis=-1)
        np.testing.assert_allclose(
            gradient_energy(rgb), gradient_energy(gray), rtol=1e-9, atol=1e-9
        )


class TestForwardCosts:
    # Hand-computed on I = [[0, 0, 0], [9, 0, 9]] with edge clamping:
    #   CU(1,:) = [|0-9|, |9-9|, |9-0|]            = [9, 0, 9]
    #   CL(1,:) = CU + |I(0,j) - I(1,j-1)|         = [18, 9, 9]
    #   CR(1,:) = CU + |I(0,j) - I(1,j+1)|         = [9, 9, 18]
    IMAGE = np.array([[0.0, 0.0, 0.0], [9.0, 0.0, 9.0]])

    def test_cu_cl_cr_hand_computed(self):
        cu, cl, cr = forward_costs(self.IMAGE)
        np.testing.assert_array_equal(cu[0], [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(cl[0], [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(cr[0], [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(cu[1], [9.0, 0.0, 9.0])
        np.testing.assert_array_equal(cl[1], [18.0, 9.0, 9.0])
        np.testing.assert_array_equal(cr[1], [9.0, 9.0, 18.0])

    def test_requires_2d(self):
        with pytest.raises(ValueError):
            forward_costs(np.zeros((2, 2, 3)))


class TestEnergySeparation:
    """Gradient energy and forward energy are different functions."""

    def test_energies_differ_on_same_image(self):
        # Hand-derived Sobel surface for [[0,0,0],[9,0,9]] (reflect borders):
        #   gx = [[-9,0,9],[-27,0,27]], gy = [[27,18,27],[27,18,27]]
        #   E  = hypot(gx, gy) = [[sqrt(810), 18, sqrt(810)],
        #                         [27*sqrt(2), 18, 27*sqrt(2)]]
        image = np.array([[0.0, 0.0, 0.0], [9.0, 0.0, 9.0]])
        energy = gradient_energy(image)
        expected_surface = np.array(
            [
                [np.sqrt(810.0), 18.0, np.sqrt(810.0)],
                [27.0 * np.sqrt(2.0), 18.0, 27.0 * np.sqrt(2.0)],
            ]
        )
        np.testing.assert_allclose(energy, expected_surface, rtol=0, atol=1e-12)

    def test_to_luminance_rejects_bad_shape(self):
        with pytest.raises(ValueError):
            to_luminance(np.zeros((2, 2, 4)))
