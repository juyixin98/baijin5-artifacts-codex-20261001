"""Image data contract.

Defines the shared vocabulary used by kernels, tiling, jobs and the API:

- :class:`BoundaryMode` — how out-of-range samples are synthesised.
- :class:`KernelSpec` — a dense 2D kernel with an *explicit anchor*.
- :class:`SeparableKernelSpec` — a rank-1 kernel given as column/row vectors.
- :class:`ImageDocument` — an image plus provenance (shape, dtype, digest).

Anchor convention
-----------------
The filter computes (correlation-form, matching ``scipy.ndimage.correlate``)::

    out[y, x] = sum_{i,j} w[i, j] * src[y + (i - ay), x + (j - ax)]

so kernel element ``(ay, ax)`` sits exactly over the output pixel.  For an odd
kernel of size ``k`` the natural anchor is ``k // 2`` and the context is
symmetric.  For an *even* kernel the anchor is still explicit: with
``anchor = k // 2`` the kernel covers offsets ``[-anchor, k - 1 - anchor]``,
e.g. ``k = 4, anchor = 2`` covers offsets ``-2, -1, 0, +1`` — two samples of
left/top context and one of right/bottom context.  The halo is derived from
these offsets, never assumed symmetric, so an even kernel never silently
loses the extra sample of context on the anchor side.

Boundary semantics
------------------
- ``MIRROR``   — half-sample symmetric reflection, edge sample repeated:
  ``... c b a | a b c | c b a ...`` (``numpy.pad(mode="symmetric")``,
  ``scipy.ndimage`` ``mode="reflect"``).
- ``CONSTANT`` — out-of-range samples are the constant ``cval``.
- ``PERIODIC`` — the image wraps around (``numpy.pad(mode="wrap")``,
  ``scipy.ndimage`` ``mode="wrap"``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np


class ContractError(ValueError):
    """Raised when data violates the image/kernel contract."""


class BoundaryMode(str, Enum):
    MIRROR = "mirror"
    CONSTANT = "constant"
    PERIODIC = "periodic"

    @classmethod
    def parse(cls, value: str) -> "BoundaryMode":
        try:
            return cls(value)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in cls)
            raise ContractError(
                f"unknown boundary mode {value!r}; expected one of: {allowed}"
            ) from exc


def _as_float_matrix(weights: Any) -> np.ndarray:
    arr = np.asarray(weights, dtype=np.float64)
    if arr.ndim != 2:
        raise ContractError(f"kernel must be 2-D, got shape {arr.shape}")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise ContractError(f"kernel must be non-empty, got shape {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ContractError("kernel contains non-finite values")
    return arr


def _check_anchor(anchor: int, size: int, axis: str) -> int:
    anchor = int(anchor)
    if not 0 <= anchor < size:
        raise ContractError(
            f"{axis} anchor {anchor} out of range for kernel size {size}"
        )
    return anchor


@dataclass(frozen=True)
class KernelSpec:
    """Dense 2D kernel with explicit anchor.

    ``weights`` is stored as nested tuples so the spec is hashable and its
    digest is stable across processes.
    """

    weights: tuple[tuple[float, ...], ...]
    anchor_y: int
    anchor_x: int

    @classmethod
    def from_array(
        cls, weights: Any, anchor: tuple[int, int] | None = None
    ) -> "KernelSpec":
        arr = _as_float_matrix(weights)
        ky, kx = arr.shape
        if anchor is None:
            anchor = (ky // 2, kx // 2)
        ay = _check_anchor(anchor[0], ky, "y")
        ax = _check_anchor(anchor[1], kx, "x")
        rows = tuple(tuple(float(v) for v in row) for row in arr.tolist())
        return cls(weights=rows, anchor_y=ay, anchor_x=ax)

    @property
    def array(self) -> np.ndarray:
        return np.asarray(self.weights, dtype=np.float64)

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.weights), len(self.weights[0]))

    @property
    def anchor(self) -> tuple[int, int]:
        return (self.anchor_y, self.anchor_x)

    @property
    def halo(self) -> tuple[int, int, int, int]:
        """(top, bottom, left, right) context required by this kernel."""
        ky, kx = self.shape
        return (self.anchor_y, ky - 1 - self.anchor_y,
                self.anchor_x, kx - 1 - self.anchor_x)

    def digest(self) -> str:
        payload = {
            "kind": "dense",
            "weights": self.weights,
            "anchor": [self.anchor_y, self.anchor_x],
        }
        return _stable_digest(payload)


@dataclass(frozen=True)
class SeparableKernelSpec:
    """Rank-1 kernel ``outer(col, row)`` with explicit per-axis anchors."""

    col: tuple[float, ...]  # vertical weights, length ky
    row: tuple[float, ...]  # horizontal weights, length kx
    anchor_y: int
    anchor_x: int

    @classmethod
    def from_vectors(
        cls,
        col: Any,
        row: Any,
        anchor: tuple[int, int] | None = None,
    ) -> "SeparableKernelSpec":
        col_arr = np.asarray(col, dtype=np.float64).ravel()
        row_arr = np.asarray(row, dtype=np.float64).ravel()
        if col_arr.size < 1 or row_arr.size < 1:
            raise ContractError("separable vectors must be non-empty")
        if not (np.all(np.isfinite(col_arr)) and np.all(np.isfinite(row_arr))):
            raise ContractError("separable vectors contain non-finite values")
        if anchor is None:
            anchor = (col_arr.size // 2, row_arr.size // 2)
        ay = _check_anchor(anchor[0], col_arr.size, "y")
        ax = _check_anchor(anchor[1], row_arr.size, "x")
        return cls(
            col=tuple(float(v) for v in col_arr.tolist()),
            row=tuple(float(v) for v in row_arr.tolist()),
            anchor_y=ay,
            anchor_x=ax,
        )

    @property
    def anchor(self) -> tuple[int, int]:
        return (self.anchor_y, self.anchor_x)

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.col), len(self.row))

    @property
    def halo(self) -> tuple[int, int, int, int]:
        ky, kx = self.shape
        return (self.anchor_y, ky - 1 - self.anchor_y,
                self.anchor_x, kx - 1 - self.anchor_x)

    def to_dense(self) -> KernelSpec:
        full = np.outer(np.asarray(self.col), np.asarray(self.row))
        return KernelSpec.from_array(full, anchor=self.anchor)

    def digest(self) -> str:
        payload = {
            "kind": "separable",
            "col": self.col,
            "row": self.row,
            "anchor": [self.anchor_y, self.anchor_x],
        }
        return _stable_digest(payload)


def _stable_digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def array_digest(data: np.ndarray) -> str:
    """Content digest of an array: shape, dtype and raw bytes."""
    arr = np.ascontiguousarray(data)
    h = hashlib.sha256()
    h.update(str(arr.shape).encode())
    h.update(arr.dtype.str.encode())
    h.update(arr.tobytes())
    return h.hexdigest()


@dataclass
class ImageDocument:
    """An image plus provenance. ``data`` is always 2-D float64."""

    data: np.ndarray
    source: str = "memory"

    def __post_init__(self) -> None:
        arr = np.asarray(self.data, dtype=np.float64)
        if arr.ndim != 2:
            raise ContractError(f"image must be 2-D, got shape {arr.shape}")
        if arr.shape[0] < 1 or arr.shape[1] < 1:
            raise ContractError(f"image must be non-empty, got shape {arr.shape}")
        if not np.all(np.isfinite(arr)):
            raise ContractError("image contains non-finite values")
        self.data = arr

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape

    def digest(self) -> str:
        return array_digest(self.data)
