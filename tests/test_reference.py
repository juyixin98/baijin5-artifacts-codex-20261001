"""The spatial NCC reference is independent of the FFT kernel and must
agree with construction ground truth and with the kernel's integer peak."""

import numpy as np

from app.kernel.pipeline import estimate_shift
from app.kernel.reference import ncc_reference


def test_reference_recovers_integer_ground_truth(fixtures):
    fx = fixtures["integer_shift"]
    ref = ncc_reference(fx.img_a, fx.img_b, max_shift=20)
    assert ref.best_shift == (12, -7)
    assert ref.best_score > 0.9
    assert ref.evaluated > 0


def test_reference_recovers_subpixel_ground_truth_integer_part(fixtures):
    fx = fixtures["subpixel_shift"]
    ref = ncc_reference(fx.img_a, fx.img_b, max_shift=12)
    assert ref.best_shift == (5, -4)  # nearest integer to (5.4, -3.65)


def test_reference_handles_brightness_change(fixtures):
    fx = fixtures["brightness_change"]
    ref = ncc_reference(fx.img_a, fx.img_b, max_shift=16)
    assert ref.best_shift == (8, 6)  # nearest integer to (8.25, 6.4)


def test_kernel_integer_peak_matches_reference(fixtures, cfg):
    for name in ("integer_shift", "subpixel_shift", "brightness_change"):
        fx = fixtures[name]
        ref = ncc_reference(fx.img_a, fx.img_b, max_shift=fx.reference_max_shift)
        est = estimate_shift(fx.img_a, fx.img_b, cfg)
        assert tuple(est.integer_shift) == tuple(ref.best_shift), name


def test_reference_rejects_mismatched_shapes():
    a = np.zeros((16, 16))
    b = np.zeros((16, 32))
    try:
        ncc_reference(a, b, max_shift=4)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
