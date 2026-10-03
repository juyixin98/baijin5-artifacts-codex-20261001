"""Energy-function tests with hand-derived reference values.

The reference arrays below were computed by hand from the documented
definitions (central differences, clamped borders, sum over channels) --
not produced by the implementation under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from seamcarve.energy import forward_energy_components, gradient_energy

HAND_4x4 = np.array(
    [
        [10, 20, 30, 40],
        [10, 20, 30, 40],
        [50, 60, 70, 80],
        [50, 60, 70, 80],
    ],
    dtype=np.uint8,
)

# hand-derived: dx rows [10,20,20,10]; dy rows [0,40,40,0] broadcast per column
HAND_GRADIENT = np.array(
    [
        [10.0, 20.0, 20.0, 10.0],
        [50.0, 60.0, 60.0, 50.0],
        [50.0, 60.0, 60.0, 50.0],
        [10.0, 20.0, 20.0, 10.0],
    ]
)

HAND_CU = np.array(
    [
        [10.0, 20.0, 20.0, 10.0],
        [10.0, 20.0, 20.0, 10.0],
        [10.0, 20.0, 20.0, 10.0],
        [10.0, 20.0, 20.0, 10.0],
    ]
)
HAND_CL = np.array(
    [
        [10.0, 30.0, 30.0, 20.0],
        [10.0, 30.0, 30.0, 20.0],
        [50.0, 50.0, 50.0, 40.0],
        [10.0, 30.0, 30.0, 20.0],
    ]
)
HAND_CR = np.array(
    [
        [20.0, 30.0, 30.0, 10.0],
        [20.0, 30.0, 30.0, 10.0],
        [60.0, 70.0, 70.0, 50.0],
        [20.0, 30.0, 30.0, 10.0],
    ]
)


def test_gradient_energy_matches_hand_derived(run_logger):
    energy = gradient_energy(HAND_4x4)
    run_logger.emit(
        "assertion", step="energy", basis="hand-derived gradient array",
        input="hand_4x4", expected=HAND_GRADIENT.tolist(), got=energy.tolist(),
    )
    np.testing.assert_array_equal(energy, HAND_GRADIENT)


def test_forward_components_match_hand_derived(run_logger):
    cu, cl, cr = forward_energy_components(HAND_4x4)
    run_logger.emit(
        "assertion", step="energy", basis="hand-derived CU/CL/CR arrays",
        input="hand_4x4",
    )
    np.testing.assert_array_equal(cu, HAND_CU)
    np.testing.assert_array_equal(cl, HAND_CL)
    np.testing.assert_array_equal(cr, HAND_CR)


def test_gradient_energy_rgb_sums_channels():
    # R channel is [[10,20],[30,40]], G and B are zero -> energy equals the
    # gradient of the R channel alone: dx=[10,10], dy=[20,20] -> 30 everywhere.
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    image[:, :, 0] = [[10, 20], [30, 40]]
    energy = gradient_energy(image)
    np.testing.assert_array_equal(energy, np.full((2, 2), 30.0))


def test_gradient_energy_1x1_is_zero():
    assert gradient_energy(np.array([[7]], dtype=np.uint8))[0, 0] == 0.0


def test_energy_dtype_and_shape(fixture_loader):
    image, _, _ = fixture_loader("rgb_10x8")
    energy = gradient_energy(image)
    assert energy.shape == (10, 8)
    assert energy.dtype == np.float64
    cu, cl, cr = forward_energy_components(image)
    for component in (cu, cl, cr):
        assert component.shape == (10, 8)
        assert (component >= 0).all()


def test_energy_rejects_bad_input():
    with pytest.raises(Exception):
        gradient_energy(np.zeros((2, 2, 2)))  # 3 channels required, not 2
