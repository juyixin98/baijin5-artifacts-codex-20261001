"""Topology probe tests: hand-built arrays with known answers."""

from __future__ import annotations

import numpy as np

from app import topology


def test_components_and_holes_known_answers():
    img = np.zeros((12, 12), dtype=np.uint8)
    img[1:4, 1:4] = 1          # solid block: component 1, no hole
    img[6:11, 6:11] = 1        # ring: component 2
    img[7:10, 7:10] = 0        # ... with a 3x3 hole
    assert topology.count_foreground_components(img) == 2
    assert topology.count_holes(img) == 1


def test_hole_touching_border_is_not_a_hole():
    img = np.zeros((6, 6), dtype=np.uint8)
    img[0:3, 0:3] = 1
    img[0, 1] = 0  # notch open to the border
    assert topology.count_holes(img) == 0


def test_endpoints_and_junctions_on_hand_drawn_skeleton():
    skel = np.zeros((7, 7), dtype=np.uint8)
    skel[3, 1:6] = 1   # horizontal bar
    skel[1:6, 3] = 1   # vertical bar -> cross
    endpoints = topology.endpoint_pixels(skel)
    assert int(endpoints.sum()) == 4
    junctions = {tuple(p) for p in np.argwhere(topology.junction_pixels(skel))}
    # the crossing pixel (deg 4) plus its four direct neighbors (deg 3)
    assert junctions == {(3, 3), (2, 3), (4, 3), (3, 2), (3, 4)}


def test_2x2_block_detection():
    skel = np.zeros((5, 5), dtype=np.uint8)
    assert not topology.has_2x2_block(skel)
    skel[1:3, 1:3] = 1
    assert topology.has_2x2_block(skel)
