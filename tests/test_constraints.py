"""Constraint contract tests: step pattern and Sakoe-Chiba window."""

import pytest

from dtw_service.constraints import SakoeChibaWindow, StepPattern


def test_default_pattern_has_no_pure_axis_steps():
    pattern = StepPattern()
    for di, dj in pattern.steps:
        assert di >= 1 and dj >= 1


def test_pure_horizontal_step_rejected():
    with pytest.raises(ValueError, match="pure-axis"):
        StepPattern(steps=((1, 1), (1, 0)))


def test_pure_vertical_step_rejected():
    with pytest.raises(ValueError, match="pure-axis"):
        StepPattern(steps=((0, 2),))


def test_zero_step_rejected():
    with pytest.raises(ValueError):
        StepPattern(steps=((0, 0),))


def test_empty_pattern_rejected():
    with pytest.raises(ValueError):
        StepPattern(steps=())


def test_window_membership():
    window = SakoeChibaWindow(radius=2)
    assert window.contains(3, 3)
    assert window.contains(3, 5)
    assert not window.contains(3, 6)
    assert window.j_range(3, 10) == (1, 5)
    assert window.j_range(0, 10) == (0, 2)
    assert window.j_range(9, 10) == (7, 9)


def test_negative_window_rejected():
    with pytest.raises(ValueError, match="radius"):
        SakoeChibaWindow(radius=-1)


def test_endpoint_reachability():
    window = SakoeChibaWindow(radius=2)
    assert window.endpoint_reachable(10, 12)
    assert not window.endpoint_reachable(10, 13)
