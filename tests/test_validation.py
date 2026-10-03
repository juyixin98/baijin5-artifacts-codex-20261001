"""Validation-interface tests: concrete failure categories, not smoke checks."""

from __future__ import annotations

import numpy as np

from app.kernel import thin
from app.samples import ring, thin_bridge
from app.validation import (
    count_components,
    count_holes,
    topology_report,
)


def test_ring_report_passes_with_exact_counts() -> None:
    image = ring()
    result = thin(image)
    report = topology_report(image, result.skeleton, converged=result.converged)
    assert report.passed
    assert report.failures == ()
    assert (report.components_before, report.components_after) == (1, 1)
    assert (report.holes_before, report.holes_after) == (1, 1)
    assert (report.endpoints_before, report.endpoints_after) == (0, 0)


def test_bridge_report_passes() -> None:
    image = thin_bridge()
    result = thin(image)
    report = topology_report(image, result.skeleton, converged=result.converged)
    assert report.passed
    assert (report.components_before, report.components_after) == (1, 1)
    assert (report.holes_before, report.holes_after) == (0, 0)


def test_broken_skeleton_flags_component_failure() -> None:
    image = thin_bridge()
    skeleton = thin(image).skeleton
    # Cut the skeleton in half along the middle column.
    broken = skeleton.copy()
    broken[:, skeleton.shape[1] // 2] = 0
    assert count_components(broken) == 2
    report = topology_report(image, broken)
    assert not report.passed
    assert any(f.startswith("component_count_changed:1->2") for f in report.failures)


def test_opened_ring_flags_hole_failure() -> None:
    image = ring()
    skeleton = thin(image).skeleton
    # Open the loop: delete a 3-pixel arc so the hole leaks to the border.
    ys, xs = np.argwhere(skeleton)[0]
    opened = skeleton.copy()
    opened[max(0, ys - 1) : ys + 2, xs] = 0
    assert count_holes(opened) == 0
    report = topology_report(image, opened)
    assert not report.passed
    assert any(f.startswith("hole_count_changed:1->0") for f in report.failures)


def test_erased_foreground_is_a_failure() -> None:
    image = thin_bridge()
    report = topology_report(image, np.zeros_like(image))
    assert not report.passed
    assert "foreground_erased:non-empty input produced empty skeleton" in report.failures


def test_empty_input_is_uncertain_not_a_failure() -> None:
    empty = np.zeros((5, 5), dtype=np.uint8)
    report = topology_report(empty, empty)
    assert report.passed
    assert "empty_foreground:topology checks are vacuous" in report.uncertain


def test_non_convergence_is_reported_as_uncertain() -> None:
    image = ring()
    result = thin(image, max_rounds=1)
    report = topology_report(image, result.skeleton, converged=result.converged)
    assert "not_converged:max_rounds reached before a clean round" in report.uncertain
