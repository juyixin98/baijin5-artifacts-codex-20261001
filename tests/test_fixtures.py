"""Fixture generation: determinism, manifest round-trip, PNG IO."""

import json

import numpy as np

from app.fixtures import build_fixtures, generate_all, load_fixtures


def test_fixtures_are_deterministic():
    first = {fx.name: fx for fx in build_fixtures()}
    second = {fx.name: fx for fx in build_fixtures()}
    for name in first:
        assert np.array_equal(first[name].img_a, second[name].img_a), name
        assert np.array_equal(first[name].img_b, second[name].img_b), name


def test_fixture_categories_cover_contract():
    cats = {fx.category for fx in build_fixtures()}
    assert {
        "integer_shift",
        "subpixel_shift",
        "brightness_change",
        "periodic_texture",
        "constant_image",
        "low_overlap",
        "independent_pair",
    } <= cats


def test_ground_truth_by_construction():
    """img_b must equal a shifted crop of img_a's canvas, verifiable
    directly: for the integer fixture, img_b[y] == img_a[y - dy] exactly
    on the overlap (integer spline shift is exact)."""
    fx = {f.name: f for f in build_fixtures()}["integer_shift"]
    dy, dx = 12, -7
    a, b = fx.img_a, fx.img_b
    overlap_a = a[max(0, -dy) : a.shape[0] - max(0, dy), max(0, -dx) : a.shape[1] - max(0, dx)]
    overlap_b = b[max(0, dy) : b.shape[0] - max(0, -dy), max(0, dx) : b.shape[1] - max(0, -dx)]
    assert np.allclose(overlap_a, overlap_b, atol=1e-8)


def test_generate_and_load_roundtrip(tmp_path):
    generate_all(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["fixtures"]) == 7
    loaded = {fx.name: fx for fx in load_fixtures(tmp_path)}
    built = {fx.name: fx for fx in build_fixtures()}
    for name, fx in loaded.items():
        # PNG round-trip quantises to 8-bit; ground truth metadata intact.
        assert fx.ground_truth_shift == built[name].ground_truth_shift
        assert fx.img_a.shape == (128, 128)
        assert np.abs(fx.img_a - built[name].img_a).max() <= 0.5
