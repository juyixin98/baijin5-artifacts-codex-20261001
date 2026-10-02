"""分块构建作业测试：层形状、逐层数值对照独立参考、结构化日志。"""
from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from pyramid_service.builder import build_pyramid
from pyramid_service.contracts import GeneratorSpec
from pyramid_service.errors import InputError
from pyramid_service.logging_utils import JsonFormatter
from pyramid_service.synth import generate_window
from pyramid_service.tilestore import TileStore

from .reference import ref_pyramid


def _build(store, spec, n_levels, tile_size, logger, run_id="run-test"):
    return build_pyramid(
        store,
        "img-test",
        spec,
        n_levels=n_levels,
        tile_size=tile_size,
        logger=logger,
        run_id=run_id,
    )


@pytest.fixture()
def file_logger(tmp_path):
    logger = logging.getLogger("test-builder")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(tmp_path / "build.jsonl")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    yield logger, tmp_path / "build.jsonl"
    logger.removeHandler(handler)


def test_build_odd_size_pyramid_matches_reference(store, file_logger):
    logger, log_path = file_logger
    spec = GeneratorSpec(kind="gradient", width=13, height=11, params={"kx": 3, "ky": 5})
    meta = _build(store, spec, n_levels=3, tile_size=4, logger=logger)

    assert [(lm.height, lm.width) for lm in meta.levels] == [(11, 13), (6, 7), (3, 4)]

    # 独立参考：整图生成 + 显式循环降采样
    full0 = generate_window(spec, 0, 0, 11, 13).astype(np.float64)
    refs = ref_pyramid(full0, 3)
    for level, ref in enumerate(refs):
        h, w = ref.shape
        got = store.read_region("img-test", level, 0, 0, w, h)
        np.testing.assert_allclose(got, ref, atol=1e-5, err_msg=f"level {level}")

    # 日志可回放：run_id 贯穿，层级发布事件齐全
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert all(e["run_id"] == "run-test" for e in events)
    names = [e["event"] for e in events]
    assert names.count("level_published") == 3
    assert names[-1] == "image_published"
    published = [e for e in events if e["event"] == "level_published"]
    assert published[0]["width"] == 13 and published[0]["height"] == 11


def test_cross_tile_stitching_exact(store, file_logger):
    # tile_size=4，读取跨 3 个瓦片的区域，与整层参考切片逐像素一致
    logger, _ = file_logger
    spec = GeneratorSpec(kind="noise", width=17, height=9, params={"seed": 99})
    _build(store, spec, n_levels=2, tile_size=4, logger=logger, run_id="run-stitch")
    full0 = generate_window(spec, 0, 0, 9, 17).astype(np.float64)
    refs = ref_pyramid(full0, 2)
    got = store.read_region("img-test", 1, x=3, y=1, w=6, h=4)  # 跨 3x2 瓦片边界
    np.testing.assert_allclose(got, refs[1][1:5, 3:9], atol=1e-5)


def test_too_many_levels_rejected(store, file_logger):
    logger, _ = file_logger
    spec = GeneratorSpec(kind="gradient", width=4, height=4)
    with pytest.raises(InputError):
        _build(store, spec, n_levels=10, tile_size=4, logger=logger)
