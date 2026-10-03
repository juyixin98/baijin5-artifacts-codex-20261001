"""Deterministic tie-breaking tests.

The documented rule: predecessor preference straight > up-left > up-right
(strict ``<`` scan), final-column ties break leftmost. Each case below is
a hand-crafted energy map where a specific tie decides the path; the
expected columns were derived by hand from the rule.
"""

from __future__ import annotations

import numpy as np
import pytest

from seamcarve.kernel import find_seam, find_vertical_seam_gradient


def test_uniform_image_ties_break_to_leftmost_straight(fixture_loader, run_logger):
    """All-zero energy: final tie -> leftmost column, then straight up."""
    image, _, descriptor = fixture_loader("uniform_5x5")
    mask = np.zeros((5, 5), bool)
    for mode in ("gradient", "forward"):
        result = find_seam(image, mask, mode)
        run_logger.emit(
            "assertion", step="dp", basis="tie-break: leftmost + straight",
            input=descriptor["name"], input_sha256=descriptor["sha256"], mode=mode,
            expected_columns=[0, 0, 0, 0, 0], got_columns=list(result.columns),
        )
        assert result.columns == (0, 0, 0, 0, 0)
        assert result.energy == pytest.approx(0.0)


def test_center_preferred_over_up_left_on_tie():
    """Hand-derived: at (2,1) and (1,1) the straight predecessor ties the
    up-left one; the rule keeps straight -> seam stays in column 1."""
    energy = np.array(
        [
            [0.0, 0.0, 9.0],
            [0.0, 0.0, 9.0],
            [9.0, 0.0, 9.0],
        ]
    )
    result = find_vertical_seam_gradient(energy, np.zeros((3, 3), bool))
    assert result.columns == (1, 1, 1)
    assert result.energy == pytest.approx(0.0)


def test_up_left_preferred_over_up_right_on_tie():
    """Hand-derived: at (2,1) up-left and up-right tie (center worse);
    up-left wins -> path goes through (1,0), then up-right into (0,1)."""
    energy = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 5.0, 0.0],
            [9.0, 0.0, 9.0],
        ]
    )
    result = find_vertical_seam_gradient(energy, np.zeros((3, 3), bool))
    assert result.columns == (1, 0, 1)
    assert result.energy == pytest.approx(0.0)


def test_ties_are_deterministic_across_calls(fixture_loader):
    image, _, _ = fixture_loader("uniform_5x5")
    mask = np.zeros((5, 5), bool)
    first = find_seam(image, mask, "gradient")
    for _ in range(5):
        assert find_seam(image, mask, "gradient").columns == first.columns
