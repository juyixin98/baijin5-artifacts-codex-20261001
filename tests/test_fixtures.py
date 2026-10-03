"""Fixture tests: synthetic generators and the PNG contract round-trip."""
from __future__ import annotations

import numpy as np

from app.contract import ImageDocument
from app.fixtures import (
    box_kernel,
    impulse_image,
    load_png,
    save_png,
    seeded_noise_image,
    step_edge_image,
    tagged_border_image,
)


def test_impulse_has_exactly_one_nonzero():
    img = impulse_image((9, 7), pos=(3, 4), value=2.5)
    assert np.count_nonzero(img) == 1
    assert img[3, 4] == 2.5


def test_step_edge_split_point():
    img = step_edge_image((4, 6), axis=1, loc=2, low=-1.0, high=3.0)
    assert np.all(img[:, :2] == -1.0)
    assert np.all(img[:, 2:] == 3.0)


def test_tagged_border_values():
    img = tagged_border_image((6, 6), border=1, border_values=(1, 2, 3, 4))
    assert img[0, 3] == 1.0   # top
    assert img[-1, 3] == 2.0  # bottom
    assert img[3, 0] == 3.0   # left
    assert img[3, -1] == 4.0  # right
    assert img[3, 3] == 0.0   # interior


def test_seeded_noise_is_reproducible():
    a = seeded_noise_image((5, 5), seed=1)
    b = seeded_noise_image((5, 5), seed=1)
    c = seeded_noise_image((5, 5), seed=2)
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_box_kernel_normalised():
    k = box_kernel(3)
    assert k.array.sum() == 1.0
    assert k.shape == (3, 3)


def test_png_roundtrip_via_pillow(tmp_path):
    img = np.linspace(0.0, 1.0, 64).reshape(8, 8)
    path = tmp_path / "fixture.png"
    save_png(path, img)
    loaded = load_png(path)
    assert loaded.shape == (8, 8)
    # 8-bit quantisation: agreement within one quantisation step
    assert np.max(np.abs(loaded - img)) <= 1.0 / 255.0 + 1e-9
    doc = ImageDocument(data=loaded, source=str(path))
    assert len(doc.digest()) == 64
