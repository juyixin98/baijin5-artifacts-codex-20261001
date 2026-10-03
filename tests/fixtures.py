"""Synthetic fixtures with hand-computed expected results.

Every ``expected`` array below is a literal derived by hand from the
definition  rec = fixpoint of  min(dilate(rec), mask)  — none of them are
produced by running the code under test.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fixture:
    name: str
    marker: np.ndarray
    mask: np.ndarray
    expected: np.ndarray


def thin_bridge() -> Fixture:
    """Two 5x3 plateaus joined by a 1-pixel-wide bridge over a zero wall."""
    mask = np.full((5, 7), 200.0)
    mask[:, 3] = 0.0          # vertical wall
    mask[2, 3] = 200.0        # 1-px bridge through the wall
    marker = np.zeros((5, 7))
    marker[2, 0] = 100.0
    expected = np.where(mask > 0, 100.0, 0.0)  # wave caps at marker height 100
    return Fixture("thin_bridge", marker, mask, expected)


def hole() -> Fixture:
    """A zero hole inside a high mask: the wave must flow around it."""
    mask = np.full((5, 5), 180.0)
    mask[2, 2] = 0.0
    marker = np.zeros((5, 5))
    marker[0, 0] = 120.0
    expected = np.full((5, 5), 120.0)
    expected[2, 2] = 0.0
    return Fixture("hole", marker, mask, expected)


def flat_zone() -> Fixture:
    """Constant mask: the whole flat zone rises to the highest marker value."""
    mask = np.full((4, 4), 90.0)
    marker = np.zeros((4, 4))
    marker[1, 1] = 60.0
    marker[3, 3] = 20.0
    expected = np.full((4, 4), 60.0)
    return Fixture("flat_zone", marker, mask, expected)


def boundary_no_wrap() -> Fixture:
    """Marker on the top row; an isolated mask pixel at the opposite corner
    must stay 0 — catches any wrap-around in the boundary handling."""
    mask = np.zeros((4, 4))
    mask[0, :] = 200.0
    mask[3, 3] = 200.0
    marker = np.zeros((4, 4))
    marker[0, 0] = 100.0
    expected = np.zeros((4, 4))
    expected[0, :] = 100.0
    return Fixture("boundary_no_wrap", marker, mask, expected)


def boundary_corner() -> Fixture:
    """Marker exactly on the corner pixel of a full mask."""
    mask = np.full((4, 4), 255.0)
    marker = np.zeros((4, 4))
    marker[0, 0] = 77.0
    expected = np.full((4, 4), 77.0)
    return Fixture("boundary_corner", marker, mask, expected)


def binary_rings() -> Fixture:
    """Binary (0/1) reconstruction: marker inside a ring mask."""
    mask = np.zeros((7, 7))
    mask[1:6, 1:6] = 1.0      # 5x5 foreground block
    marker = np.zeros((7, 7))
    marker[3, 3] = 1.0
    expected = mask.copy()    # fills the whole connected foreground
    return Fixture("binary_rings", marker, mask, expected)


ALL_FIXTURES = [thin_bridge, hole, flat_zone, boundary_no_wrap, boundary_corner, binary_rings]
