"""Kernel-level tests: dead-bin handling, window/padding declarations, sign convention."""
import numpy as np

from app.kernel import phase_correlation as pc


def test_dead_bins_are_zeroed_not_divided():
    """Constant image: only the DC bin survives; no NaN/Inf may appear."""
    img = np.full((32, 32), 7.0)
    cp = pc.cross_power_spectrum(img, img)
    assert np.all(np.isfinite(cp.normalized))
    # exactly one alive bin (DC) out of 32*32
    assert cp.zero_bin_fraction == 1.0 - 1.0 / cp.n_bins
    assert abs(cp.normalized[0, 0] - 1.0) < 1e-12
    assert np.count_nonzero(cp.normalized) == 1


def test_all_zero_image_reports_full_dead_spectrum():
    cp = pc.cross_power_spectrum(np.zeros((16, 16)), np.zeros((16, 16)))
    assert cp.zero_bin_fraction == 1.0
    assert not np.any(cp.normalized)


def test_hann_window_has_zero_borders():
    w = pc.hann_window((16, 24))
    assert w.shape == (16, 24)
    assert np.all(w[0, :] == 0) and np.all(w[-1, :] == 0)
    assert np.all(w[:, 0] == 0) and np.all(w[:, -1] == 0)
    assert w.max() <= 1.0


def test_declared_padding_extends_unambiguous_range():
    """A genuine (non-circular) shift of 20 px on 32 px images is only
    resolvable because pad_factor=2 extends the range to +/-32."""
    rng = np.random.default_rng(0)
    base = rng.standard_normal((96, 32))
    ref = base[0:32, :]
    mov = base[20:52, :]          # mov[y] == ref[y + 20] -> shift (dy, dx) = (-20, 0)
    surf = pc.correlation_surface(ref, mov, apply_window=False, pad_factor=2)
    assert surf.padded_shape == (64, 64)
    _, shift = pc.integer_peak(surf.values)
    assert shift == (-20.0, 0.0)


def test_sign_convention_matches_docstring():
    """moving[y, x] == reference[y - dy, x - dx] must yield peak at (dy, dx)."""
    rng = np.random.default_rng(1)
    ref = rng.standard_normal((64, 64))
    dy, dx = 7, -11
    mov = np.roll(ref, shift=(dy, dx), axis=(0, 1))
    surf = pc.correlation_surface(ref, mov, apply_window=False, pad_factor=1)
    _, shift = pc.integer_peak(surf.values)
    assert shift == (float(dy), float(dx))


def test_negative_shift_aliases_correctly():
    rng = np.random.default_rng(2)
    ref = rng.standard_normal((64, 64))
    mov = np.roll(ref, shift=(-9, 13), axis=(0, 1))
    surf = pc.correlation_surface(ref, mov, apply_window=False, pad_factor=1)
    _, shift = pc.integer_peak(surf.values)
    assert shift == (-9.0, 13.0)


def test_shape_mismatch_raises():
    with np.testing.assert_raises(ValueError):
        pc.correlation_surface(np.zeros((16, 16)), np.zeros((16, 32)))
