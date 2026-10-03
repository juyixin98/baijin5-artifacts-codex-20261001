"""Alpha semantics: split/join, premultiply round-trips, engine preservation."""

from __future__ import annotations

import numpy as np

from iccconv.contract import AlphaMode, ColorSpace, ImageDocument, RenderingIntent
from iccconv.kernel.alpha import join_alpha, premultiply, split_alpha, unpremultiply
from iccconv.kernel.engine import convert_document


def test_split_and_join_roundtrip():
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 256, size=(5, 6, 4), dtype=np.uint8)
    color, alpha = split_alpha(pixels, ColorSpace.RGB, AlphaMode.STRAIGHT)
    assert color.shape == (5, 6, 3)
    assert alpha.shape == (5, 6)
    assert np.array_equal(join_alpha(color, alpha), pixels)


def test_unpremultiply_premultiply_roundtrip():
    rng = np.random.default_rng(11)
    color = rng.integers(0, 256, size=(64, 3), dtype=np.uint8)
    alpha = rng.integers(1, 256, size=(64,), dtype=np.uint8)  # avoid div-by-zero case here
    prem = premultiply(color, alpha)
    back = unpremultiply(prem, alpha)
    # Analytic bound: two quantizations give |back - c| <= 255/(2*alpha) + 1.
    err = np.abs(back.astype(float) - color.astype(float))
    bound = 255.0 / (2.0 * alpha.astype(float))[:, None] + 1.0
    assert (err <= bound).all()


def test_unpremultiply_zero_alpha_defined():
    color = np.array([[200, 100, 50]], dtype=np.uint8)
    alpha = np.array([0], dtype=np.uint8)
    out = unpremultiply(color, alpha)
    assert out.tolist() == [[0, 0, 0]]  # defined behavior, no NaN/garbage


def test_premultiply_zero_alpha_gives_zero():
    color = np.array([[200, 100, 50]], dtype=np.uint8)
    alpha = np.array([0], dtype=np.uint8)
    assert premultiply(color, alpha).tolist() == [[0, 0, 0]]


def _rgba_edge_image() -> np.ndarray:
    """Opaque-to-transparent horizontal edge with distinct colors."""
    pixels = np.zeros((4, 8, 4), dtype=np.uint8)
    pixels[..., 0] = 255  # red channel everywhere
    pixels[..., 3] = np.tile(np.linspace(0, 255, 8, dtype=np.uint8), (4, 1))
    return pixels


def test_alpha_preserved_bit_exact_through_conversion(registry):
    pixels = _rgba_edge_image()
    doc = ImageDocument(pixels, ColorSpace.RGB, AlphaMode.STRAIGHT)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("AdobeRGB1998.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    assert np.array_equal(result.document.pixels[..., 3], pixels[..., 3])
    assert result.document.alpha_mode is AlphaMode.STRAIGHT


def test_premultiplied_matches_straight_path(registry):
    rng = np.random.default_rng(3)
    straight_color = rng.integers(0, 256, size=(6, 6, 3), dtype=np.uint8)
    alpha = rng.integers(0, 256, size=(6, 6), dtype=np.uint8)
    prem = premultiply(straight_color, alpha)

    src = registry.load("sRGB.icc")
    dst = registry.load("AdobeRGB1998.icc")
    kwargs = dict(intent=RenderingIntent.RELATIVE_COLORIMETRIC)

    via_straight = convert_document(
        ImageDocument(join_alpha(straight_color, alpha), ColorSpace.RGB, AlphaMode.STRAIGHT),
        source=src, target=dst, alpha_mode_out=AlphaMode.PREMULTIPLIED, **kwargs,
    ).document.pixels
    via_premultiplied = convert_document(
        ImageDocument(join_alpha(prem, alpha), ColorSpace.RGB, AlphaMode.PREMULTIPLIED),
        source=src, target=dst, alpha_mode_out=AlphaMode.PREMULTIPLIED, **kwargs,
    ).document.pixels

    # Same fixed semantics: both paths unpremultiply -> convert -> repremultiply.
    # The only difference is quantization of the unpremultiplied input (<=2 LSB
    # before conversion), so results agree within a small tolerance.
    assert np.abs(via_straight.astype(int) - via_premultiplied.astype(int)).max() <= 3
