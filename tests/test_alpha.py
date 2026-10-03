"""Alpha semantics: separation, passthrough, premultiplied handling, edges."""
from __future__ import annotations

import numpy as np

from colorconvert.contract import ColorMode, ImageData
from colorconvert.kernel import RenderingIntent, _premultiply, _unpremultiply

REL = RenderingIntent.RELATIVE_COLORIMETRIC


def _profiles(registry):
    return (
        registry.get("srgb", role="source"),
        registry.get("adobe-rgb", role="target"),
    )


def test_alpha_passes_through_bit_identical(kernel, registry):
    src, dst = _profiles(registry)
    rng = np.random.default_rng(7)
    color = rng.integers(0, 256, (6, 6, 3), dtype=np.uint8)
    alpha = rng.integers(0, 256, (6, 6), dtype=np.uint8)
    image = ImageData(color=color, mode=ColorMode.RGB, alpha=alpha)
    result, _ = kernel.convert(image, src, dst, REL, False)
    assert np.array_equal(result.alpha, alpha)


def test_color_conversion_independent_of_alpha_when_straight(kernel, registry):
    src, dst = _profiles(registry)
    color = np.full((2, 2, 3), (10, 200, 30), np.uint8)
    a = ImageData(color=color, mode=ColorMode.RGB,
                  alpha=np.full((2, 2), 255, np.uint8))
    b = ImageData(color=color, mode=ColorMode.RGB,
                  alpha=np.full((2, 2), 3, np.uint8))
    ra, _ = kernel.convert(a, src, dst, REL, False)
    rb, _ = kernel.convert(b, src, dst, REL, False)
    assert np.array_equal(ra.color, rb.color)


def test_premultiplied_roundtrip_matches_straight_path(kernel, registry):
    src, dst = _profiles(registry)
    rng = np.random.default_rng(11)
    straight = rng.integers(0, 256, (5, 5, 3), dtype=np.uint8)
    alpha = rng.integers(1, 256, (5, 5), dtype=np.uint8)  # avoid div-by-zero

    prem = ImageData(
        color=_premultiply(straight, alpha),
        mode=ColorMode.RGB,
        alpha=alpha,
        premultiplied=True,
    )
    got, _ = kernel.convert(prem, src, dst, REL, False)

    ref_straight, _ = kernel.convert(
        ImageData(color=straight, mode=ColorMode.RGB), src, dst, REL, False
    )
    expected = _premultiply(ref_straight.color, alpha)
    # unpremultiply->convert->repremultiply in 8-bit loses <=1 LSB per step
    assert np.abs(got.color.astype(int) - expected.astype(int)).max() <= 2


def test_fully_transparent_pixels_are_defined(kernel, registry):
    src, dst = _profiles(registry)
    color = np.array([[(12, 34, 56)]], dtype=np.uint8)
    alpha = np.array([[0]], dtype=np.uint8)
    prem = ImageData(
        color=_premultiply(color, alpha),
        mode=ColorMode.RGB,
        alpha=alpha,
        premultiplied=True,
    )
    result, _ = kernel.convert(prem, src, dst, REL, False)
    assert result.color.shape == (1, 1, 3)  # no crash, defined output
    assert result.alpha[0, 0] == 0


def test_transparent_edge_no_color_bleed(kernel, registry):
    """Per-pixel transform: changing one side of an alpha edge must not
    alter converted pixels on the other side (zero bleed by construction)."""
    src, dst = _profiles(registry)
    base = np.zeros((4, 8, 3), np.uint8)
    base[:, :4] = (200, 30, 30)
    base[:, 4:] = (30, 30, 200)
    alpha = np.zeros((4, 8), np.uint8)
    alpha[:, :4] = 255  # sharp alpha edge down the middle

    variant = base.copy()
    variant[:, 4:] = (99, 99, 10)  # change only the right side

    ra, _ = kernel.convert(
        ImageData(color=base, mode=ColorMode.RGB, alpha=alpha), src, dst, REL, False
    )
    rb, _ = kernel.convert(
        ImageData(color=variant, mode=ColorMode.RGB, alpha=alpha), src, dst, REL, False
    )
    assert np.array_equal(ra.color[:, :4], rb.color[:, :4])
    assert not np.array_equal(ra.color[:, 4:], rb.color[:, 4:])


def test_unpremultiply_math():
    # valid premultiplied data: every color channel <= alpha
    color = np.array([[(100, 60, 120)]], dtype=np.uint8)
    alpha = np.array([[128]], dtype=np.uint8)
    straight = _unpremultiply(color, alpha)
    # 100*255/128 = 199.2, 60*255/128 = 119.5, 120*255/128 = 239.1
    assert straight[0, 0].tolist() == [199, 120, 239]
    back = _premultiply(straight, alpha)
    assert np.abs(back.astype(int) - color.astype(int)).max() <= 1


def test_unpremultiply_zero_alpha_gives_zero():
    color = np.array([[(0, 0, 0)]], dtype=np.uint8)
    alpha = np.array([[0]], dtype=np.uint8)
    assert _unpremultiply(color, alpha)[0, 0].tolist() == [0, 0, 0]
