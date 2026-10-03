"""Kernel correctness: hand-computed cases, an independent naive reference,
and Pillow's BOX (area) resampler as a third-party anchor."""

import numpy as np
import pytest
from PIL import Image

from pyramid_service.errors import ComputeError, InputValidationError
from pyramid_service.kernel import (
    GAUSSIAN_MARGIN,
    downsample_2x,
    downsample_area_2x,
    downsample_gaussian_2x,
    kernel_margin,
)
from pyramid_service.patterns import checkerboard, diagonal_lines

from conftest import naive_area_2x


def test_area_checkerboard_even_is_uniform_midgray():
    src = checkerboard(8, 8, period=1)
    out = downsample_area_2x(src)
    assert out.shape == (4, 4, 1)
    np.testing.assert_allclose(out, 0.5, atol=1e-12)


def test_area_odd_size_handcomputed():
    # 3 rows x 5 cols; expected values computed by hand (block means with
    # clipped, renormalized edge blocks).
    src = np.arange(15, dtype=np.float64).reshape(3, 5, 1)
    out = downsample_area_2x(src)
    assert out.shape == (2, 3, 1)
    expected = np.array(
        [
            [3.0, 5.0, 6.5],   # means of {0,1,5,6}, {2,3,7,8}, {4,9}
            [10.5, 12.5, 14.0],  # means of {10,11}, {12,13}, {14}
        ]
    )
    np.testing.assert_allclose(out[..., 0], expected, atol=1e-12)


def test_area_matches_naive_reference_on_odd_random():
    rng = np.random.default_rng(42)
    src = rng.random((47, 63, 1))
    out = downsample_area_2x(src)
    assert out.shape == (24, 32, 1)
    np.testing.assert_allclose(out, naive_area_2x(src), atol=1e-12)


def test_area_matches_pillow_box_even():
    # Third-party reference: Pillow BOX == area resampling at exact scale 2.
    rng = np.random.default_rng(7)
    src = rng.random((48, 64)).astype(np.float32)
    ref = np.asarray(
        Image.fromarray(src, mode="F").resize((32, 24), Image.BOX)
    )
    out = downsample_area_2x(src.astype(np.float64)[..., None])[..., 0]
    np.testing.assert_allclose(out, ref, atol=1e-3)


def test_area_preserves_energy_on_diagonal_lines():
    src = diagonal_lines(32, 32, spacing=4, thickness=1)
    out = downsample_area_2x(src)
    # Area averaging conserves total intensity: sum(out) == sum(src) / 4.
    assert out.sum() == pytest.approx(src.sum() / 4.0, rel=1e-12)


def test_area_rgb_channels_independent():
    rng = np.random.default_rng(3)
    src = rng.random((10, 12, 3))
    out = downsample_area_2x(src)
    assert out.shape == (5, 6, 3)
    for ch in range(3):
        np.testing.assert_allclose(
            out[..., ch], naive_area_2x(src[..., ch : ch + 1])[..., 0], atol=1e-12
        )


def test_gaussian_constant_image_is_exact():
    src = np.full((9, 11, 1), 0.7)
    out = downsample_gaussian_2x(src)
    assert out.shape == (5, 6, 1)
    np.testing.assert_allclose(out, 0.7, atol=1e-12)


def test_gaussian_checkerboard_converges_to_midgray():
    src = checkerboard(16, 16, period=1)
    out = downsample_gaussian_2x(src)
    np.testing.assert_allclose(out, 0.5, atol=0.05)


def test_gaussian_blob_is_centered_and_symmetric():
    # 2x2 white block centered on the level-1 sampling grid (axis at 7.5,
    # which the fixed centers 2d+0.5 reflect into themselves).
    src = np.zeros((16, 16, 1))
    src[7:9, 7:9, 0] = 1.0
    out = downsample_gaussian_2x(src)
    assert out.shape == (8, 8, 1)
    np.testing.assert_allclose(out[..., 0], out[..., 0][::-1, ::-1], atol=1e-12)
    # The peak sits on the central 2x2 outputs and falls off outwards.
    center = out[3:5, 3:5, 0]
    np.testing.assert_allclose(center, center.max(), atol=1e-12)
    assert center.max() > out[0, 0, 0]
    assert center.max() > out[3, 2, 0] > out[3, 0, 0]


def test_kernel_rejects_bad_input():
    with pytest.raises(ComputeError):
        downsample_area_2x(np.zeros((4, 4)))  # 2D, contract requires (H,W,C)
    with pytest.raises(ComputeError):
        downsample_area_2x(np.zeros((4, 4, 1), dtype=np.uint8))  # not float
    with pytest.raises(ComputeError):
        downsample_area_2x(np.zeros((0, 4, 1)))
    with pytest.raises(InputValidationError):
        downsample_2x(np.zeros((4, 4, 1)), kernel="bicubic")
    with pytest.raises(InputValidationError):
        kernel_margin("bicubic")


def test_kernel_margins():
    assert kernel_margin("area") == 0
    assert GAUSSIAN_MARGIN > 0 and GAUSSIAN_MARGIN % 2 == 0
