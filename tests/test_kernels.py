"""Kernel contract tests."""
from __future__ import annotations

import numpy as np
import pytest

from app.core import kernels


@pytest.mark.parametrize("name,fn", [(k, f) for k, f in kernels.KERNELS.items()])
def test_kernels_are_symmetric_compact_and_nonnegative(name, fn):
    u = np.linspace(-1.5, 1.5, 601)
    w = fn(u)
    # hard compact support
    assert np.all(w[u > 1.0] == 0.0)
    assert np.all(w[u < -1.0] == 0.0)
    assert np.all(w >= 0.0)
    # symmetry: K(u) == K(-u)
    pos = u >= 0
    np.testing.assert_allclose(fn(u[pos]), fn(-u[pos]), atol=1e-12)


@pytest.mark.parametrize("name", list(kernels.KERNELS))
def test_kernels_integrate_to_one_on_minus_one_one(name):
    # 2 * integral_0^1 K(u) du must equal 1 for a density on [-1, 1].
    fn = kernels.KERNELS[name]
    grid = np.linspace(0.0, 1.0, 200001)
    integral = 2.0 * np.trapezoid(fn(grid), grid)
    assert integral == pytest.approx(1.0, abs=1e-4), name


def test_triangular_values_exact():
    fn = kernels.triangular
    np.testing.assert_allclose(
        fn(np.array([0.0, 0.25, 1.0, 1.5])),
        np.array([1.0, 0.75, 0.0, 0.0]),
    )


def test_get_kernel_unknown_fails_loudly():
    with pytest.raises(ValueError, match="unknown kernel"):
        kernels.get_kernel("gauss-magic")
    with pytest.raises(TypeError):
        kernels.get_kernel(7)  # type: ignore[arg-type]


def test_kernel_selection_is_case_insensitive():
    assert kernels.get_kernel("Triangular") is kernels.triangular
