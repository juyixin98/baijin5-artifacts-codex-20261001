"""Coefficient normalization and stability-check tests."""

import numpy as np
import pytest

from app.dsp.coefficients import normalize_and_validate, section_pole_radii
from app.errors import CoefficientError, ResourceLimitError

LIMITS = dict(max_sections=8, pole_radius_limit=1.0)


def test_a0_zero_rejected(runlog):
    sos = [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]
    with pytest.raises(CoefficientError) as exc:
        normalize_and_validate(sos, **LIMITS)
    runlog("reject", reason="a0 == 0 must be rejected", code=exc.value.code)
    assert exc.value.code == "invalid_coefficients"
    assert exc.value.category.value == "input_error"


def test_nonfinite_coefficients_rejected():
    with pytest.raises(CoefficientError):
        normalize_and_validate([[1.0, np.nan, 0.0, 1.0, 0.0, 0.0]], **LIMITS)
    with pytest.raises(CoefficientError):
        normalize_and_validate([[1.0, 0.0, 0.0, np.inf, 0.0, 0.0]], **LIMITS)


def test_unstable_poles_rejected(runlog):
    # denominator 1 - 2 z^-1 -> pole at z = 2 (outside unit circle)
    sos = [[1.0, 0.0, 0.0, 1.0, -2.0, 0.0]]
    with pytest.raises(CoefficientError) as exc:
        normalize_and_validate(sos, **LIMITS)
    runlog("reject", reason="pole radius 2.0 >= 1.0", context=exc.value.context)
    assert exc.value.context["pole_radius"] == pytest.approx(2.0)


def test_marginal_pole_on_unit_circle_rejected():
    # denominator 1 - 2 z^-1 + z^-2 -> double pole exactly at z = 1
    sos = [[1.0, 0.0, 0.0, 1.0, -2.0, 1.0]]
    with pytest.raises(CoefficientError):
        normalize_and_validate(sos, **LIMITS)


def test_pole_near_unit_circle_accepted(runlog):
    # poles at r=0.999, angle +-pi/4: a1 = -2 r cos, a2 = r^2
    r, theta = 0.999, np.pi / 4
    sos = [[1.0, 0.0, 0.0, 1.0, -2 * r * np.cos(theta), r * r]]
    norm = normalize_and_validate(sos, **LIMITS)
    runlog("accept", reason="pole radius 0.999 < 1.0",
           max_pole_radius=norm.max_pole_radius)
    assert norm.max_pole_radius == pytest.approx(r, rel=1e-12)


def test_normalization_divides_by_a0():
    sos = [[2.0, 4.0, 2.0, 2.0, -1.0, 0.5]]
    norm = normalize_and_validate(sos, **LIMITS)
    row = norm.sections[0]
    assert row[3] == 1.0
    assert row[:3].tolist() == [1.0, 2.0, 1.0]
    assert row[4:] .tolist() == [-0.5, 0.25]


def test_too_many_sections_is_resource_error():
    sos = [[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]] * 9
    with pytest.raises(ResourceLimitError):
        normalize_and_validate(sos, **LIMITS)


def test_bad_shape_rejected():
    with pytest.raises(CoefficientError):
        normalize_and_validate([[1.0, 0.0, 0.0]], **LIMITS)
    with pytest.raises(CoefficientError):
        normalize_and_validate([], **LIMITS)


def test_section_pole_radii_matches_known_roots():
    # z^2 - z + 0.25 = (z - 0.5)^2
    radii = section_pole_radii(-1.0, 0.25)
    assert np.allclose(np.sort(radii), [0.5, 0.5])
