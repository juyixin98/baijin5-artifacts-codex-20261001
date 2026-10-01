"""Unit tests for kernel functions."""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import KernelName
from app.kernels import epanechnikov, triangular, uniform, weights


@pytest.mark.unit
def test_kernels_are_non_negative_symmetric_and_compact() -> None:
    u = np.linspace(-2, 2, 401)
    for fn in (triangular, epanechnikov, uniform):
        w = fn(u)
        assert np.all(w >= 0.0)
        # symmetry around zero
        np.testing.assert_allclose(fn(u), fn(-u), atol=1e-12)
        # compact support: zero beyond 1
        assert np.all(w[np.abs(u) > 1.0 + 1e-12] == 0.0)


@pytest.mark.unit
def test_triangular_boundary_and_peak() -> None:
    # weight 1 at the cutoff, 0 at the window edge, linear between
    assert triangular(np.array([0.0]))[0] == pytest.approx(1.0)
    assert triangular(np.array([1.0]))[0] == pytest.approx(0.0)
    assert triangular(np.array([0.5]))[0] == pytest.approx(0.5)


@pytest.mark.unit
def test_weights_scale_with_bandwidth() -> None:
    x = np.array([0.0, 0.5, 1.0, 1.5])
    w = weights(x, cutoff=0.0, bandwidth=1.0, kernel=KernelName.TRIANGULAR)
    np.testing.assert_allclose(w, [1.0, 0.5, 0.0, 0.0])
    w2 = weights(x, cutoff=0.0, bandwidth=2.0, kernel=KernelName.TRIANGULAR)
    np.testing.assert_allclose(w2, [1.0, 0.75, 0.5, 0.25])


@pytest.mark.unit
def test_weights_reject_nonpositive_bandwidth() -> None:
    with pytest.raises(ValueError):
        weights(np.array([0.0]), 0.0, 0.0, KernelName.UNIFORM)
