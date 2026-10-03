"""Tile store: atomic publication, integrity checks, failure categories."""

import json

import numpy as np
import pytest

from pyramid_service.errors import (
    ComputeError,
    NotFoundError,
    StateConflictError,
    TileIntegrityError,
)

from conftest import constant_tiles, make_meta


def test_pyramid_id_cannot_escape_store_root(store):
    from pyramid_service.errors import InputValidationError

    for bad in ("../etc", "a/b", "..", "", "a..b", " spaces ", ".hidden"):
        with pytest.raises(InputValidationError):
            store.init_pyramid(bad, {})
        with pytest.raises(InputValidationError):
            store.load_manifest(bad, 0)


def test_incomplete_tile_iterator_refuses_to_publish(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=16, height=8, tile_size=8)  # expects 2 tiles
    one_tile_only = [next(iter(constant_tiles(meta)))]
    with pytest.raises(ComputeError, match="incomplete level"):
        store.publish_level("p1", meta, one_tile_only)
    assert store.list_levels("p1") == []
    with pytest.raises(NotFoundError):
        store.load_manifest("p1", 0)


def test_publish_and_read_roundtrip(store):
    store.init_pyramid("p1", {"run_id": "r0"})
    meta = make_meta(level=0, width=16, height=8, tile_size=8)
    published = store.publish_level("p1", meta, constant_tiles(meta))
    assert len(published.tiles) == 2  # 2x1 grid

    loaded = store.load_manifest("p1", 0)
    assert loaded.width == 16 and loaded.tiles_x == 2
    tile = store.read_tile("p1", 0, 1, 0)
    assert tile.shape == (8, 8, 1)
    np.testing.assert_array_equal(tile, 1.0)  # value = 0 + 0*10 + 1
    assert store.list_levels("p1") == [0]


def test_republication_is_a_state_conflict(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=8, height=8, tile_size=8)
    store.publish_level("p1", meta, constant_tiles(meta))
    with pytest.raises(StateConflictError):
        store.publish_level("p1", meta, constant_tiles(meta))
    with pytest.raises(StateConflictError):
        store.init_pyramid("p1", {})


def test_missing_pyramid_and_level_are_not_found(store):
    with pytest.raises(NotFoundError):
        store.load_manifest("nope", 0)
    store.init_pyramid("p1", {})
    with pytest.raises(NotFoundError):
        store.load_manifest("p1", 3)
    with pytest.raises(NotFoundError):
        store.list_levels("nope")


def test_corrupt_tile_fails_integrity_instead_of_returning_pixels(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=16, height=8, tile_size=8)
    store.publish_level("p1", meta, constant_tiles(meta))

    tile_path = store.level_dir("p1", 0) / "tiles" / "0_1.npy"
    tile_path.write_bytes(b"\xde\xad\xbe\xef" * 64)

    with pytest.raises(TileIntegrityError):
        store.read_tile("p1", 0, 1, 0)
    report = store.verify_level("p1", 0)
    assert report["ok"] is False
    assert report["checked"] == 2
    assert len(report["bad"]) == 1
    assert (report["bad"][0]["tx"], report["bad"][0]["ty"]) == (1, 0)
    assert "sha256" in report["bad"][0]["reason"]


def test_missing_tile_file_is_an_integrity_error(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=8, height=8, tile_size=8)
    store.publish_level("p1", meta, constant_tiles(meta))
    (store.level_dir("p1", 0) / "tiles" / "0_0.npy").unlink()
    with pytest.raises(TileIntegrityError):
        store.read_tile("p1", 0, 0, 0)


def test_corrupt_manifest_is_an_integrity_error(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=8, height=8, tile_size=8)
    store.publish_level("p1", meta, constant_tiles(meta))
    store._manifest_cache.clear()
    (store.level_dir("p1", 0) / "manifest.json").write_text("{not json")
    with pytest.raises(TileIntegrityError):
        store.load_manifest("p1", 0)


def test_failed_publish_leaves_no_visible_level(store, monkeypatch):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=16, height=8, tile_size=8)

    def boom(*args, **kwargs):
        raise ComputeError("simulated crash mid-publish")

    monkeypatch.setattr(store, "_write_tile", boom)
    with pytest.raises(ComputeError):
        store.publish_level("p1", meta, constant_tiles(meta))

    assert store.list_levels("p1") == []
    with pytest.raises(NotFoundError):
        store.load_manifest("p1", 0)
    # Staging directory must not be mistaken for a level later.
    assert not (store.pyramid_dir("p1") / "levels" / "0").exists()


def test_tile_shape_mismatch_is_a_compute_error(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=8, height=8, tile_size=8)
    bad = [(0, 0, np.zeros((4, 4, 1)))]
    with pytest.raises(ComputeError):
        store.publish_level("p1", meta, bad)


def test_manifest_records_run_id_and_checksums(store):
    store.init_pyramid("p1", {})
    meta = make_meta(level=0, width=8, height=8, tile_size=8, run_id="run-abc")
    store.publish_level("p1", meta, constant_tiles(meta))
    raw = json.loads((store.level_dir("p1", 0) / "manifest.json").read_text())
    assert raw["run_id"] == "run-abc"
    assert raw["tiles"][0]["sha256"]
    assert len(raw["tiles"][0]["sha256"]) == 64
