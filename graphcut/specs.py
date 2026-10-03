"""Build :class:`EnergySpec` contracts from request payloads.

This is the boundary between the HTTP schema and the numerical core: every
request mode (image-derived unaries or explicit arrays; Potts / contrast-
sensitive / explicit pairwise tables) is materialized here into the single
explicit form the kernel understands, so there is exactly one code path
downstream.

Pairwise modes:
    * ``potts``:    V(0,1) = V(1,0) = weight, V(0,0) = V(1,1) = 0
    * ``contrast``: weight * exp(-beta * (I_p - I_q)^2) on unequal labels
    * ``explicit``: caller supplies (v00, v01, v10, v11) per edge — the only
                    mode that can be non-submodular, hence the one the
                    submodularity rejection is exercised through

All built-in modes are submodular by construction (non-negative weights on
unequal labels only).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .energy import validate_spec
from .errors import InputValidationError
from .models import EnergySpec, ImageContract, PairwiseTerm, SeedSet


def neighbor_pairs(width: int, height: int) -> list[tuple[int, int]]:
    """4-neighborhood edges (right + down only, no duplicates, no wrap)."""
    pairs: list[tuple[int, int]] = []
    for r in range(height):
        for c in range(width):
            p = r * width + c
            if c + 1 < width:
                pairs.append((p, p + 1))
            if r + 1 < height:
                pairs.append((p, p + width))
    return pairs


def build_unaries(
    unary_spec: dict[str, Any],
    *,
    width: int,
    height: int,
    image: ImageContract | None,
) -> tuple[np.ndarray, np.ndarray]:
    mode = unary_spec.get("mode")
    if mode == "explicit":
        u0 = _as_grid(unary_spec.get("cost0"), "cost0", width, height)
        u1 = _as_grid(unary_spec.get("cost1"), "cost1", width, height)
        return u0, u1
    if mode == "image_model":
        if image is None:
            raise InputValidationError(
                "missing_image",
                "unary mode 'image_model' requires an image",
            )
        if image.width != width or image.height != height:
            raise InputValidationError(
                "image_size_mismatch",
                f"image is {image.width}x{image.height}, spec says "
                f"{width}x{height}",
            )
        fg_mean = _finite_float(unary_spec, "fg_mean")
        bg_mean = _finite_float(unary_spec, "bg_mean")
        sigma = _finite_float(unary_spec, "sigma")
        if sigma <= 0.0:
            raise InputValidationError(
                "bad_sigma", f"sigma must be positive, got {sigma}"
            )
        # negative log-likelihoods of a Gaussian intensity model, shifted so
        # the cheaper label costs 0 per pixel (keeps terms non-negative)
        diff_fg = (image.pixels - fg_mean) ** 2 / (2.0 * sigma * sigma)
        diff_bg = (image.pixels - bg_mean) ** 2 / (2.0 * sigma * sigma)
        floor = np.minimum(diff_fg, diff_bg)
        return diff_bg - floor, diff_fg - floor
    raise InputValidationError(
        "bad_unary_mode", f"unknown unary mode {mode!r}"
    )


def build_pairwise(
    pairwise_spec: dict[str, Any],
    *,
    width: int,
    height: int,
    image: ImageContract | None,
) -> tuple[PairwiseTerm, ...]:
    mode = pairwise_spec.get("mode")
    if mode == "explicit":
        terms = []
        for raw in pairwise_spec.get("edges", []):
            try:
                terms.append(
                    PairwiseTerm(
                        p=int(raw["p"]),
                        q=int(raw["q"]),
                        v00=float(raw["v00"]),
                        v01=float(raw["v01"]),
                        v10=float(raw["v10"]),
                        v11=float(raw["v11"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise InputValidationError(
                    "bad_pairwise_edge",
                    f"malformed explicit pairwise edge {raw!r}: {exc}",
                ) from exc
        return tuple(terms)

    weight = _finite_float(pairwise_spec, "weight")
    if weight < 0.0:
        raise InputValidationError(
            "negative_smoothness_term",
            f"pairwise weight must be non-negative, got {weight}",
        )
    pairs = neighbor_pairs(width, height)
    if mode == "potts":
        return tuple(
            PairwiseTerm(p, q, 0.0, weight, weight, 0.0) for p, q in pairs
        )
    if mode == "contrast":
        if image is None:
            raise InputValidationError(
                "missing_image", "pairwise mode 'contrast' requires an image"
            )
        beta = _finite_float(pairwise_spec, "beta")
        if beta < 0.0:
            raise InputValidationError(
                "bad_beta", f"beta must be non-negative, got {beta}"
            )
        flat = image.pixels.reshape(-1)
        terms = []
        for p, q in pairs:
            w = weight * float(np.exp(-beta * (flat[p] - flat[q]) ** 2))
            terms.append(PairwiseTerm(p, q, 0.0, w, w, 0.0))
        return tuple(terms)
    raise InputValidationError(
        "bad_pairwise_mode", f"unknown pairwise mode {mode!r}"
    )


def build_spec(request: dict[str, Any]) -> EnergySpec:
    """Parse a raw request dict into a validated :class:`EnergySpec`."""
    try:
        width = int(request["width"])
        height = int(request["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputValidationError(
            "bad_dimensions", f"request needs integer width/height: {exc}"
        ) from exc
    if width <= 0 or height <= 0:
        raise InputValidationError(
            "bad_dimensions", f"width/height must be positive, got {width}x{height}"
        )

    image: ImageContract | None = request.get("_image")
    unary0, unary1 = build_unaries(
        request.get("unary") or {}, width=width, height=height, image=image
    )
    pairwise = build_pairwise(
        request.get("pairwise") or {"mode": "potts", "weight": 0.0},
        width=width, height=height, image=image,
    )
    seeds_raw = request.get("seeds") or {}
    seeds = SeedSet(
        foreground=tuple(int(i) for i in seeds_raw.get("foreground", [])),
        background=tuple(int(i) for i in seeds_raw.get("background", [])),
    )
    spec = EnergySpec(
        width=width, height=height,
        unary0=unary0, unary1=unary1,
        pairwise=pairwise, seeds=seeds,
    )
    validate_spec(spec)
    return spec


def _as_grid(raw: Any, name: str, width: int, height: int) -> np.ndarray:
    if raw is None:
        raise InputValidationError(
            "bad_unary_shape", f"explicit unary mode requires '{name}'"
        )
    arr = np.asarray(raw, dtype=np.float64)
    if arr.shape != (height, width):
        raise InputValidationError(
            "bad_unary_shape",
            f"'{name}' must have shape {(height, width)}, got {arr.shape}",
        )
    return arr


def _finite_float(mapping: dict[str, Any], key: str) -> float:
    try:
        value = float(mapping[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputValidationError(
            "bad_parameter", f"parameter '{key}' must be a number: {exc}"
        ) from exc
    if not np.isfinite(value):
        raise InputValidationError(
            "bad_parameter", f"parameter '{key}' must be finite, got {value}"
        )
    return value
