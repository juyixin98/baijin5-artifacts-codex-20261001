"""层间坐标映射与层尺寸序列测试。"""
from __future__ import annotations

import pytest

from pyramid_service.kernel import (
    level_shape,
    level_shapes,
    max_levels_for,
    pixel_center_to_source,
    pixel_support_source,
    source_to_pixel_center,
)


def test_level_shapes_odd_size_chain():
    # 7x5 -> 4x3 -> 2x2 -> 1x1：奇数尺寸边界不丢行列
    assert level_shapes(7, 5, 4) == [(7, 5), (4, 3), (2, 2), (1, 1)]


def test_level_shape_is_ceil_division():
    assert level_shape(1, 1, 0) == (1, 1)
    assert level_shape(9, 9, 1) == (5, 5)
    assert level_shape(513, 257, 3) == (65, 33)


def test_level_size_recurrence():
    for h, w in [(2, 2), (3, 7), (100, 101)]:
        for lv in range(max_levels_for(h, w) - 1):
            h1, w1 = level_shape(h, w, lv)
            h2, w2 = level_shape(h, w, lv + 1)
            assert (h2, w2) == ((h1 + 1) // 2, (w1 + 1) // 2)


def test_max_levels_reaches_single_pixel():
    assert max_levels_for(1, 1) == 1
    assert max_levels_for(7, 5) == 4
    assert max_levels_for(256, 256) == 9


def test_pixel_center_mapping_fixed_values():
    assert pixel_center_to_source(0, 0, 0) == (0.0, 0.0)
    assert pixel_center_to_source(0, 0, 1) == (0.5, 0.5)
    assert pixel_center_to_source(2, 3, 2) == (9.5, 13.5)
    # 逆映射往返一致
    assert source_to_pixel_center(9.5, 13.5, 2) == (2.0, 3.0)


def test_every_source_pixel_covered_no_dropped_rows_or_cols():
    # 奇数尺寸下，所有输出像素支持区间的并集必须恰好铺满源图
    for h, w in [(7, 5), (13, 11), (9, 1), (1, 9)]:
        for lv in range(max_levels_for(h, w)):
            lh, lw = level_shape(h, w, lv)
            covered = set()
            for r in range(lh):
                for c in range(lw):
                    (r0, r1), (c0, c1) = pixel_support_source(r, c, lv, h, w)
                    assert r1 > r0 and c1 > c0  # 无空支持（不丢行列）
                    for sr in range(r0, r1):
                        for sc in range(c0, c1):
                            covered.add((sr, sc))
            assert covered == {(sr, sc) for sr in range(h) for sc in range(w)}


def test_invalid_level_rejected():
    with pytest.raises(ValueError):
        level_shape(4, 4, -1)
