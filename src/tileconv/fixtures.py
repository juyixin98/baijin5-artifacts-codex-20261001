"""Reusable synthetic fixtures.

All fixtures are deterministic (seeded) and local — no external data. They
cover the acceptance matrix: impulses (anchor/response checks), edge-valued
images (boundary checks), ramps/steps (structure), seeded noise (general
comparison) and constants (constant-boundary checks).
"""

from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

from .errors import InvalidSpecError

FIXTURE_KINDS = (
    "impulse",
    "impulse_offcenter",
    "edge_values",
    "ramp",
    "random",
    "steps",
    "constant",
)


def synthesize(kind: str, shape: Sequence[int], seed: int = 0) -> np.ndarray:
    """Synthesize a deterministic float64 fixture image of the given shape."""
    h, w = int(shape[0]), int(shape[1])
    if h < 1 or w < 1:
        raise InvalidSpecError("fixture shape must be positive", {"shape": [h, w]})

    if kind == "impulse":
        arr = np.zeros((h, w))
        arr[h // 2, w // 2] = 1.0
    elif kind == "impulse_offcenter":
        arr = np.zeros((h, w))
        arr[h // 3, w // 5] = 1.0
    elif kind == "edge_values":
        # Distinct value per border/corner so boundary handling is observable.
        arr = np.zeros((h, w))
        arr[0, :] = 1.0
        arr[-1, :] = 2.0
        arr[:, 0] = 3.0
        arr[:, -1] = 4.0
        arr[0, 0] = 5.0
        arr[0, -1] = 6.0
        arr[-1, 0] = 7.0
        arr[-1, -1] = 8.0
    elif kind == "ramp":
        r, c = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        arr = (r + 2.0 * c) / max(h + 2.0 * w, 1.0)
    elif kind == "random":
        rng = np.random.default_rng(seed)
        arr = rng.standard_normal((h, w))
    elif kind == "steps":
        arr = np.zeros((h, w))
        arr[: h // 2, : w // 2] = 0.25
        arr[: h // 2, w // 2 :] = 0.75
        arr[h // 2 :, : w // 2] = -0.5
        arr[h // 2 :, w // 2 :] = 1.0
    elif kind == "constant":
        arr = np.full((h, w), 7.0)
    else:
        raise InvalidSpecError("unknown fixture kind",
                               {"kind": kind, "known": list(FIXTURE_KINDS)})
    return np.asarray(arr, dtype=np.float64)


def make_kernel_weights(kind: str, size: int) -> np.ndarray:
    """Deterministic 1-D kernel weights used by tests and the validation script."""
    if kind == "box":
        return np.full(size, 1.0 / size)
    if kind == "gaussian":
        x = np.arange(size) - (size - 1) / 2.0
        sigma = max(size / 6.0, 0.5)
        g = np.exp(-0.5 * (x / sigma) ** 2)
        return g / g.sum()
    if kind == "asymmetric":
        rng = np.random.default_rng(size)
        return rng.standard_normal(size)
    raise InvalidSpecError("unknown kernel weights kind", {"kind": kind})
