"""Coefficient contract tests: normalization, a0 rejection, stability gate."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter

from app.dsp.coefficients import normalize_and_validate
from app.errors import CoefficientValidationError, ResourceLimitError


def test_normalization_divides_by_a0(run_log):
    sos = [[2.0, 4.0, 2.0, 2.0, -1.0, 0.5]]
    norm = normalize_and_validate(sos)
    assert norm.sections.shape == (1, 6)
    assert norm.sections[0, 3] == 1.0
    np.testing.assert_allclose(norm.sections[0], [1.0, 2.0, 1.0, 1.0, -0.5, 0.25])
    run_log.log("normalized", row=norm.sections[0].tolist(),
                pole_radius=norm.pole_radii[0])


def test_zero_a0_rejected(run_log):
    with pytest.raises(CoefficientValidationError) as exc:
        normalize_and_validate([[1.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
    run_log.log("rejected", reason=str(exc.value), category=exc.value.category.value)
    assert "a0" in str(exc.value)


def test_denormal_a0_rejected():
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([[1.0, 0.0, 0.0, 1e-320, 0.0, 0.0]])


def test_non_finite_coefficients_rejected():
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([[1.0, float("nan"), 0.0, 1.0, 0.0, 0.0]])
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([[1.0, 0.0, 0.0, 1.0, float("inf"), 0.0]])


def test_bad_shape_rejected():
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([[1.0, 0.0, 0.0, 1.0, 0.0]])  # 5 columns
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([])  # no sections
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([1.0, 2.0, 3.0])  # 1-D


def test_unstable_pole_rejected(run_log):
    # z^2 - 2z + 0.5 has roots 1 +/- sqrt(0.5); the larger is ~1.707 > 1.
    unstable = [[1.0, 0.0, 0.0, 1.0, -2.0, 0.5]]
    with pytest.raises(CoefficientValidationError) as exc:
        normalize_and_validate(unstable)
    run_log.log("rejected", reason=str(exc.value),
                pole_radius=exc.value.detail["pole_radius"])
    assert exc.value.detail["pole_radius"] > 1.0


def test_pole_on_unit_circle_rejected():
    # z^2 - 2z + 1 = (z - 1)^2: double pole exactly at |p| = 1.
    with pytest.raises(CoefficientValidationError):
        normalize_and_validate([[1.0, 0.0, 0.0, 1.0, -2.0, 1.0]])


def test_poles_near_unit_circle_accepted(run_log):
    # 8th-order Butterworth lowpass at 0.05*Nyquist: poles very close to |z|=1.
    sos = butter(8, 0.05, output="sos")
    norm = normalize_and_validate(sos)
    assert norm.max_pole_radius < 1.0
    assert norm.max_pole_radius > 0.9  # genuinely near the circle
    run_log.log("accepted", n_sections=norm.n_sections,
                max_pole_radius=norm.max_pole_radius)


def test_section_limit_enforced():
    from app.config import MAX_SECTIONS

    too_many = [[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]] * (MAX_SECTIONS + 1)
    with pytest.raises(ResourceLimitError):
        normalize_and_validate(too_many)
