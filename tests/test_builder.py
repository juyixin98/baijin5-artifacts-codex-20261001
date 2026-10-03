"""Build jobs: odd-size pyramids, level-by-level references, run-id logs,
and the state-conflict / resource-exhaustion failure categories."""

import logging

import numpy as np
import pytest
from PIL import Image

from pyramid_service.builder import BuildSpec, SourceSpec, build_pyramid
from pyramid_service.contracts import RegionSpec
from pyramid_service.errors import ResourceExhaustedError, StateConflictError
from pyramid_service.patterns import noise
from pyramid_service.region import read_region

from conftest import cascade_reference


def _spec(pid, pattern="checkerboard", w=63, h=47, tile_size=16, levels=0,
          kernel="area"):
    return BuildSpec(
        pyramid_id=pid,
        source=SourceSpec(kind="synthetic", pattern=pattern, width=w, height=h),
        tile_size=tile_size,
        levels=levels,
        kernel=kernel,
    )


def test_odd_size_pyramid_level_dims(store, settings):
    report = build_pyramid(store, _spec("odd"), settings)
    dims = [(m.width, m.height) for m in report.levels]
    assert dims == [(63, 47), (32, 24), (16, 12), (8, 6), (4, 3), (2, 2), (1, 1)]


def test_built_levels_match_direct_cascade(store, settings):
    src = noise(47, 63, seed=9)
    path = settings.store_dir.parent / "src.npy"
    np.save(path, src)
    spec = BuildSpec(
        pyramid_id="casc",
        source=SourceSpec(kind="npy", path=str(path)),
        tile_size=8,
        levels=4,
    )
    build_pyramid(store, spec, settings)
    refs = cascade_reference(src, 4, "area")
    for level, ref in enumerate(refs):
        h, w = ref.shape[:2]
        out = read_region(store, "casc", RegionSpec(level, 0, 0, w, h))
        np.testing.assert_allclose(out, ref, atol=1e-12,
                                   err_msg=f"level {level} mismatch")


def test_built_level2_matches_pillow_box_even(store, settings):
    # Independent anchor: for even sizes a 2-step area cascade equals a
    # direct 4x area resample, which Pillow BOX reproduces.
    src = noise(48, 64, seed=13)
    path = settings.store_dir.parent / "src2.npy"
    np.save(path, src)
    spec = BuildSpec(
        pyramid_id="pillow",
        source=SourceSpec(kind="npy", path=str(path)),
        tile_size=16,
        levels=3,
    )
    build_pyramid(store, spec, settings)
    out = read_region(store, "pillow", RegionSpec(2, 0, 0, 16, 12))
    ref = np.asarray(
        Image.fromarray(src[..., 0].astype(np.float32), mode="F").resize(
            (16, 12), Image.BOX
        )
    )
    np.testing.assert_allclose(out[..., 0], ref, atol=1e-3)


def test_build_logs_run_id_and_decisions(store, settings, caplog):
    with caplog.at_level(logging.INFO, logger="pyramid_service"):
        report = build_pyramid(store, _spec("logged"), settings)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert report.run_id in text
    assert "build_start" in text
    assert "level_published" in text
    assert "build_done" in text
    assert "auto levels to 1x1" in text  # decision rationale is recorded


def test_run_id_is_published_in_manifest(store, settings):
    report = build_pyramid(store, _spec("rid", levels=2), settings)
    meta = store.load_manifest("rid", 1)
    assert meta.run_id == report.run_id


def test_rebuild_same_id_is_a_state_conflict(store, settings):
    build_pyramid(store, _spec("dup", levels=1), settings)
    with pytest.raises(StateConflictError):
        build_pyramid(store, _spec("dup", levels=1), settings)


def test_oversized_source_is_resource_exhaustion(tmp_path):
    from pyramid_service.config import Settings
    from pyramid_service.store import TileStore

    small = Settings(store_dir=tmp_path / "s2", max_source_pixels=100)
    store2 = TileStore(small.store_dir)
    with pytest.raises(ResourceExhaustedError):
        build_pyramid(store2, _spec("big", w=64, h=64), small)


def test_checkerboard_deep_level_is_midgray(store, settings):
    # A 1px checkerboard averaged down to 1x1 must be the global mean 0.5.
    build_pyramid(store, _spec("chk", w=64, h=64, tile_size=16), settings)
    out = read_region(store, "chk", RegionSpec(6, 0, 0, 1, 1))
    assert out.shape == (1, 1, 1)
    assert out[0, 0, 0] == pytest.approx(0.5, abs=1e-12)


def test_levels_beyond_1x1_is_an_input_error(store, settings):
    from pyramid_service.errors import InputValidationError

    with pytest.raises(InputValidationError, match="useful maximum"):
        build_pyramid(store, _spec("toomany", w=8, h=8, tile_size=4, levels=5),
                      settings)


def test_failed_build_rolls_back_and_id_can_be_retried(store, settings,
                                                       monkeypatch, caplog):
    import pyramid_service.builder as builder_module
    from pyramid_service.errors import ComputeError

    real = builder_module._build_level

    def flaky(*args, **kwargs):
        if args[5] == 2:  # the level argument
            raise ComputeError("simulated level-2 failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(builder_module, "_build_level", flaky)
    spec = _spec("retry", w=32, h=32, tile_size=8, levels=4)
    with caplog.at_level(logging.INFO, logger="pyramid_service"):
        with pytest.raises(ComputeError):
            build_pyramid(store, spec, settings)
    # Nothing is left behind: no partial levels, the id is free again.
    assert not store.pyramid_dir("retry").exists()
    assert "build_rolled_back" in "\n".join(
        r.getMessage() for r in caplog.records
    )

    monkeypatch.undo()
    report = build_pyramid(store, spec, settings)
    assert [m.level for m in report.levels] == [0, 1, 2, 3]


def test_malformed_npy_source_is_an_input_error(store, settings, tmp_path):
    from pyramid_service.errors import InputValidationError

    bad = tmp_path / "bad.npy"
    bad.write_text("this is not a numpy file")
    spec = BuildSpec(
        pyramid_id="badnpy",
        source=SourceSpec(kind="npy", path=str(bad)),
        tile_size=8,
        levels=1,
    )
    with pytest.raises(InputValidationError, match="not a valid .npy"):
        build_pyramid(store, spec, settings)


def test_gaussian_kernel_on_odd_sizes(store, settings):
    src = noise(13, 21, seed=17)  # odd in both axes
    path = settings.store_dir.parent / "odd_g.npy"
    np.save(path, src)
    spec = BuildSpec(
        pyramid_id="oddg",
        source=SourceSpec(kind="npy", path=str(path)),
        tile_size=8,
        levels=3,
        kernel="gaussian",
    )
    build_pyramid(store, spec, settings)
    refs = cascade_reference(src, 3, "gaussian")
    for level, ref in enumerate(refs):
        h, w = ref.shape[:2]
        out = read_region(store, "oddg", RegionSpec(level, 0, 0, w, h))
        np.testing.assert_allclose(out, ref, atol=1e-12,
                                   err_msg=f"level {level} mismatch")


def test_three_channel_end_to_end(store, settings):
    src = noise(12, 20, channels=3, seed=23)
    path = settings.store_dir.parent / "rgb.npy"
    np.save(path, src)
    spec = BuildSpec(
        pyramid_id="rgb",
        source=SourceSpec(kind="npy", path=str(path)),
        tile_size=8,
        levels=2,
    )
    report = build_pyramid(store, spec, settings)
    assert report.levels[1].channels == 3
    out = read_region(store, "rgb", RegionSpec(1, 2, 1, 6, 4))
    ref = cascade_reference(src, 2, "area")[1]
    assert out.shape == (4, 6, 3)
    np.testing.assert_allclose(out, ref[1:5, 2:8], atol=1e-12)
