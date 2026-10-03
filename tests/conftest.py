"""Shared synthetic fixtures: ring, thin bridge, fork, cross-tile stroke.

All shapes are hand-built with numpy only; expected topology (components,
holes, endpoints) is known by construction, so tests assert concrete numbers
rather than re-deriving them from the code under test.
"""

from __future__ import annotations

import numpy as np
import pytest


def draw_thick_line(img: np.ndarray, p0, p1, radius: int = 1) -> None:
    """Rasterize a line p0->p1 (row, col) and stamp a square brush."""
    steps = int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1]))) * 2 + 1
    for t in np.linspace(0, 1, steps):
        r = int(round(p0[0] + t * (p1[0] - p0[0])))
        c = int(round(p0[1] + t * (p1[1] - p0[1])))
        r0, r1 = max(0, r - radius), min(img.shape[0], r + radius + 1)
        c0, c1 = max(0, c - radius), min(img.shape[1], c + radius + 1)
        img[r0:r1, c0:c1] = 1


def ring_image() -> np.ndarray:
    """15x15, square ring of thickness 2 -> 1 component, 1 hole, 0 endpoints."""
    img = np.zeros((15, 15), dtype=np.uint8)
    img[3:13, 3:13] = 1
    img[5:11, 5:11] = 0
    return img


def thin_bridge_image() -> np.ndarray:
    """9x21, single-pixel horizontal line -> already a skeleton, 2 endpoints."""
    img = np.zeros((9, 21), dtype=np.uint8)
    img[4, 2:19] = 1
    return img


def fork_image() -> np.ndarray:
    """20x16 Y-shape with thick strokes -> 1 component, 0 holes, 3 endpoints."""
    img = np.zeros((20, 16), dtype=np.uint8)
    draw_thick_line(img, (2, 7), (11, 7), radius=1)     # stem
    draw_thick_line(img, (11, 7), (18, 2), radius=1)    # left branch
    draw_thick_line(img, (11, 7), (18, 13), radius=1)   # right branch
    return img


def cross_tile_stroke_image() -> np.ndarray:
    """40x40 thick diagonal stroke; with tile_size=8 it crosses many tiles."""
    img = np.zeros((40, 40), dtype=np.uint8)
    draw_thick_line(img, (3, 3), (36, 30), radius=1)
    draw_thick_line(img, (36, 30), (36, 37), radius=1)
    return img


def circle_ring_image() -> np.ndarray:
    """17x17 circular ring -> skeleton is a clean degree-2 loop."""
    img = np.zeros((17, 17), dtype=np.uint8)
    r, c = np.mgrid[0:17, 0:17]
    d = np.sqrt((r - 8) ** 2 + (c - 8) ** 2)
    img[(d <= 6.5) & (d >= 3.5)] = 1
    return img


@pytest.fixture
def ring() -> np.ndarray:
    return ring_image()


@pytest.fixture
def thin_bridge() -> np.ndarray:
    return thin_bridge_image()


@pytest.fixture
def fork() -> np.ndarray:
    return fork_image()


@pytest.fixture
def cross_tile_stroke() -> np.ndarray:
    return cross_tile_stroke_image()


@pytest.fixture
def circle_ring() -> np.ndarray:
    return circle_ring_image()
