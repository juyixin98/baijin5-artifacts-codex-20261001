"""瓦片存储测试：原子发布、校验和、损坏检测。"""
from __future__ import annotations

import numpy as np
import pytest

from pyramid_service.contracts import LevelMeta
from pyramid_service.errors import (
    IntegrityError,
    LevelNotFoundError,
    StateConflictError,
)
from pyramid_service.tilestore import TileStore


def _meta(level=0, w=8, h=8, ts=4):
    return LevelMeta(
        level=level,
        width=w,
        height=h,
        tile_size=ts,
        tiles_x=(w + ts - 1) // ts,
        tiles_y=(h + ts - 1) // ts,
    )


def _tiles_8x8():
    rng = np.random.default_rng(1)
    full = rng.random((8, 8)).astype(np.float32)
    for ty in range(2):
        for tx in range(2):
            yield ty, tx, full[ty * 4 : ty * 4 + 4, tx * 4 : tx * 4 + 4]


def test_publish_and_read_region_roundtrip(store: TileStore):
    tiles = list(_tiles_8x8())
    store.publish_level("img1", _meta(), tiles)
    full = np.block(
        [[tiles[0][2], tiles[1][2]], [tiles[2][2], tiles[3][2]]]
    )
    # 全层读取
    np.testing.assert_array_equal(store.read_region("img1", 0, 0, 0, 8, 8), full)
    # 跨 2x2 瓦片的奇数偏移区域
    np.testing.assert_array_equal(store.read_region("img1", 0, 3, 2, 4, 3), full[2:5, 3:7])
    # 单像素
    np.testing.assert_array_equal(
        store.read_region("img1", 0, 7, 7, 1, 1), full[7:8, 7:8]
    )


def test_missing_level_raises_not_found(store: TileStore):
    with pytest.raises(LevelNotFoundError):
        store.read_region("img-nope", 0, 0, 0, 1, 1)


def test_double_publish_is_state_conflict(store: TileStore):
    store.publish_level("img1", _meta(), _tiles_8x8())
    with pytest.raises(StateConflictError):
        store.publish_level("img1", _meta(), _tiles_8x8())


def test_corrupt_tile_raises_integrity_not_black(store: TileStore):
    store.publish_level("img1", _meta(), _tiles_8x8())
    tile_path = store.level_dir("img1", 0) / "tiles" / "0001_0000.npy"
    data = bytearray(tile_path.read_bytes())
    data[-8] ^= 0xFF  # 翻转末尾字节，破坏内容
    tile_path.write_bytes(bytes(data))
    with pytest.raises(IntegrityError):
        store.read_region("img1", 0, 0, 4, 4, 4)  # 命中损坏瓦片
    # 未损坏的瓦片仍可正常读取
    ok = store.read_region("img1", 0, 0, 0, 4, 4)
    assert ok.shape == (4, 4)


def test_failed_publish_leaves_no_partial_level(store: TileStore):
    def exploding_tiles():
        yield 0, 0, np.zeros((4, 4), dtype=np.float32)
        raise RuntimeError("simulated crash mid-publish")

    with pytest.raises(RuntimeError):
        store.publish_level("img1", _meta(), exploding_tiles())
    # 最终目录不存在，临时目录已清理
    assert not store.level_dir("img1", 0).exists()
    leftovers = list(store.levels_dir("img1").iterdir())
    assert leftovers == []
    with pytest.raises(LevelNotFoundError):
        store.read_level_meta("img1", 0)


def test_unpublished_tmp_dir_not_visible(store: TileStore):
    # 手工伪造一个未 rename 的临时目录：读者必须视为层级缺失
    tmp = store.levels_dir("img1") / ".0.tmp-deadbeef"
    (tmp / "tiles").mkdir(parents=True)
    with pytest.raises(LevelNotFoundError):
        store.read_level_meta("img1", 0)
