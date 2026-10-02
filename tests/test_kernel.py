"""数值内核测试：手工期望值 + 独立参考实现双重核验。"""
from __future__ import annotations

import numpy as np
import pytest

from pyramid_service.kernel import area_downsample2

from .reference import ref_downsample2


def test_hand_computed_even_block():
    img = np.arange(16, dtype=np.float32).reshape(4, 4)
    # [[0,1,2,3],[4,5,6,7],[8,9,10,11],[12,13,14,15]]
    expected = np.array([[2.5, 4.5], [10.5, 12.5]], dtype=np.float32)
    np.testing.assert_array_equal(area_downsample2(img), expected)


def test_hand_computed_odd_edges_kept():
    # 3x5：奇数行/列不得丢弃，边缘块按实际覆盖像素平均
    img = np.arange(15, dtype=np.float32).reshape(3, 5)
    expected = np.array(
        [
            [3.0, 5.0, 6.5],   # (0+1+5+6)/4, (2+3+7+8)/4, (4+9)/2
            [10.5, 12.5, 14.0],  # (10+11)/2, (12+13)/2, 14
        ],
        dtype=np.float32,
    )
    out = area_downsample2(img)
    assert out.shape == (2, 3)
    np.testing.assert_array_equal(out, expected)


def test_single_row_and_single_column():
    np.testing.assert_array_equal(
        area_downsample2(np.array([[1.0, 2.0, 3.0]], dtype=np.float32)),
        np.array([[1.5, 3.0]], dtype=np.float32),
    )
    np.testing.assert_array_equal(
        area_downsample2(np.array([[1.0], [2.0], [3.0]], dtype=np.float32)),
        np.array([[1.5], [3.0]], dtype=np.float32),
    )


def test_constant_image_stays_constant_odd_size():
    out = area_downsample2(np.full((7, 7), 5.0, dtype=np.float32))
    assert out.shape == (4, 4)
    np.testing.assert_array_equal(out, np.full((4, 4), 5.0, dtype=np.float32))


def test_checkerboard_averages_to_midgray():
    img = ((np.indices((8, 8)).sum(axis=0) % 2) * 255).astype(np.float32)
    out = area_downsample2(img)
    np.testing.assert_array_equal(out, np.full((4, 4), 127.5, dtype=np.float32))


def test_sum_preserved_on_even_sizes():
    rng = np.random.default_rng(42)
    img = rng.random((8, 6)).astype(np.float32)
    out = area_downsample2(img)
    assert out.sum() == pytest.approx(img.sum() / 4.0, rel=1e-6)


def test_matches_independent_reference_odd_random():
    rng = np.random.default_rng(7)
    img = (rng.random((13, 11)) * 255).astype(np.float32)
    out = area_downsample2(img)
    ref = ref_downsample2(img)
    assert out.shape == ref.shape == (7, 6)
    np.testing.assert_allclose(out, ref, atol=1e-5)


def test_rejects_non_2d():
    with pytest.raises(ValueError):
        area_downsample2(np.zeros((2, 2, 3), dtype=np.float32))
