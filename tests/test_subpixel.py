"""Subpixel refinement: parabola math, failure conditions, end-to-end accuracy."""

import numpy as np
import pytest

from app.kernel.pipeline import estimate_shift
from app.kernel.subpixel import (
    FIT_OUT_OF_RANGE,
    NON_CONCAVE,
    PEAK_ON_BORDER,
    estimate_subpixel,
    parabolic_extremum,
)


def test_parabolic_extremum_known_values():
    off, ok = parabolic_extremum(0.8, 1.0, 0.9)
    assert ok
    assert off == pytest.approx(0.5 * (0.8 - 0.9) / (0.8 - 2.0 + 0.9))


def test_parabolic_extremum_rejects_non_concave():
    off, ok = parabolic_extremum(0.9, 1.0, 1.1)  # convex -> no maximum
    assert not ok
    assert off == 0.0


def test_peak_on_border_failure():
    surface = np.zeros((9, 9))
    surface[0, 4] = 1.0
    res = estimate_subpixel(surface, 0, 4)
    assert not res.ok
    assert res.failures == [PEAK_ON_BORDER]


def test_non_concave_neighbourhood_failure():
    surface = np.zeros((9, 9))
    surface[4, 4] = 1.0
    surface[4, 5] = 2.0  # centre is not a local maximum along x
    res = estimate_subpixel(surface, 4, 4)
    assert not res.ok
    assert NON_CONCAVE in res.failures


def test_fit_out_of_range_failure():
    surface = np.zeros((9, 9))
    # Linear path (min sample == 0 skips the log fit), concave, but the
    # vertex lies beyond +/-1 px: offset = 0.5*(0-1.5)/(0-2+1.5) = 1.5.
    surface[3, 4] = 0.0
    surface[4, 4] = 1.0
    surface[5, 4] = 1.5
    res = estimate_subpixel(surface, 4, 4)
    assert not res.ok
    assert FIT_OUT_OF_RANGE in res.failures
    assert res.offset == (0.0, 0.0)


def test_subpixel_shift_end_to_end(fixtures, cfg):
    fx = fixtures["subpixel_shift"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status == "ok"
    dy, dx = est.shift
    assert abs(dy - 5.4) < 0.15
    assert abs(dx - (-3.65)) < 0.15
    assert est.confidence >= cfg.confidence_ok
