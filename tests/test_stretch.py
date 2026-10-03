"""Stretch-rate tests, including the unbounded (vertical segment) case."""

import numpy as np
import pytest

from dtw_service.stretch import local_stretch_rates, mean_stretch_rate


def test_diagonal_path_has_unit_rate():
    path = [(i, i) for i in range(10)]
    rates = local_stretch_rates(path, window_steps=2)
    assert np.allclose(rates, 1.0)
    assert mean_stretch_rate(path) == pytest.approx(1.0)


def test_doubled_reference_has_rate_two():
    path = [(i, 2 * i) for i in range(10)]
    rates = local_stretch_rates(path, window_steps=2)
    assert np.allclose(rates, 2.0)
    assert mean_stretch_rate(path) == pytest.approx(2.0)


def test_vertical_segment_yields_unbounded_rate():
    path = [(0, 0), (0, 1), (0, 2), (1, 3), (2, 4)]
    rates = local_stretch_rates(path, window_steps=1)
    assert np.isinf(rates[1])  # window around a pure vertical step
    assert np.isfinite(rates[-1])


def test_empty_path_rejected():
    with pytest.raises(ValueError):
        local_stretch_rates([])
