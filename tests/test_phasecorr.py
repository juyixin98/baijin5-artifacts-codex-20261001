"""Kernel-level tests: sign convention, zero-magnitude bins, valid range."""

import numpy as np
import pytest

from app.config import KernelConfig
from app.kernel.phasecorr import cross_power_surface
from app.kernel.windows import make_window


def _pair(shift, size=128, seed=1):
    from scipy.ndimage import shift as ndi_shift

    rng = np.random.default_rng(seed)
    canvas = rng.normal(size=(size * 2, size * 2))
    moved = ndi_shift(canvas, shift, order=3, mode="nearest")
    o = size // 2
    return canvas[o : o + size, o : o + size], moved[o : o + size, o : o + size]


def test_integer_shift_exact_and_sign_convention(cfg):
    # img_b[y, x] = img_a[y - 12, x + 7] -> expected shift (dy, dx) = (12, -7)
    a, b = _pair((12.0, -7.0))
    res = cross_power_surface(a, b, cfg)
    assert res.integer_shift == (12, -7)


def test_valid_region_shape_and_no_wraparound(cfg):
    a, b = _pair((3.0, 3.0))
    res = cross_power_surface(a, b, cfg)
    h, w = a.shape
    assert res.surface.shape == (2 * (h // 2) + 1, 2 * (w // 2) + 1)
    assert res.max_shift == (h // 2, w // 2)


def test_constant_image_fully_degenerate(cfg):
    const = np.full((128, 128), 128.0)
    res = cross_power_surface(const, const, cfg)
    # Every cross-power bin is exactly zero: nothing may be divided.
    assert res.degenerate_fraction == 1.0
    assert res.eps == 0.0
    assert res.peak_value == 0.0


def test_degenerate_bins_counted_not_divided(cfg):
    a, b = _pair((2.0, 2.0))
    res = cross_power_surface(a, b, cfg)
    assert 0 <= res.degenerate_bins <= res.total_bins
    assert np.all(np.isfinite(res.surface))


def test_window_is_explicit(cfg):
    w = make_window((32, 32), "hann")
    assert w[0, 0] == 0.0  # Hann endpoints vanish
    assert np.allclose(make_window((8, 8), "none"), 1.0)
    with pytest.raises(ValueError):
        make_window((8, 8), "blackman")
    # Config rejects nothing here, but the kernel honours the declared kind.
    no_window = KernelConfig(window="none")
    a, b = _pair((5.0, 5.0))
    assert cross_power_surface(a, b, no_window).integer_shift == (5, 5)


def test_shape_mismatch_rejected(cfg):
    a = np.zeros((32, 32))
    b = np.zeros((32, 48))
    with pytest.raises(ValueError, match="shape mismatch"):
        cross_power_surface(a, b, cfg)
