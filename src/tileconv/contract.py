"""Data contracts: boundary modes, anchor semantics, kernel/image specs, digests.

Anchor semantics (convolution form)
-----------------------------------
A 1-D kernel ``k`` of length ``n`` with anchor ``a`` produces::

    out[i] = sum_j in[i - j + a] * k[j]

i.e. the anchor is the kernel tap that lands on the output sample. The
default anchor is ``(n - 1) // 2``:

* odd ``n``  -> exact center sample (``n // 2``)
* even ``n`` -> the left-of-center sample (``n // 2 - 1``), matching the
  default placement of ``scipy.ndimage`` filters (``origin=0``).

Halo
----
For convolution form, output sample ``i`` reads inputs
``[i - (n - 1 - a), i + a]``, so the required context is
``before = n - 1 - a`` samples and ``after = a`` samples, with
``before + after == n - 1`` always. Even-sized kernels therefore keep one
more sample of *left/top* context than of right/bottom context; the halo
computation never drops a side.

Digests
-------
Image digests are SHA-256 over ``shape | dtype | raw bytes`` (streamed in row
chunks so memmap files are never fully resident). Kernel digests are SHA-256
over the canonical spec (kind, shapes, anchors, raw float64 weight bytes).
Job resume re-verifies both digests before touching any tile.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence, Tuple

import numpy as np

from .errors import InvalidSpecError


class BoundaryMode(str, Enum):
    """Out-of-range sample semantics.

    MIRROR:   whole-sample symmetric reflection, edge sample NOT repeated
              (index -1 -> 1, -2 -> 2; equivalent to np.pad "reflect" and
              scipy.ndimage mode "mirror").
    CONSTANT: fill with a constant value ``cval``.
    PERIODIC: wrap-around modulo indexing (index -1 -> n-1).
    """

    MIRROR = "mirror"
    CONSTANT = "constant"
    PERIODIC = "periodic"


def default_anchor(size: int) -> int:
    """Default anchor (origin sample) for a 1-D kernel of length ``size``."""
    if size < 1:
        raise InvalidSpecError("kernel size must be >= 1", {"size": size})
    return (size - 1) // 2


def _validate_1d(weights: Sequence[float], anchor: Optional[int], name: str) -> Tuple[Tuple[float, ...], int]:
    w = tuple(float(x) for x in weights)
    if len(w) < 1:
        raise InvalidSpecError(f"{name} kernel must be non-empty")
    if not all(np.isfinite(w)):
        raise InvalidSpecError(f"{name} kernel contains non-finite values")
    a = default_anchor(len(w)) if anchor is None else int(anchor)
    if not 0 <= a < len(w):
        raise InvalidSpecError(
            f"{name} anchor {a} out of range for size {len(w)}",
            {"anchor": a, "size": len(w)},
        )
    return w, a


def digest_array(arr: np.ndarray, chunk_bytes: int = 4 * 1024 * 1024) -> str:
    """SHA-256 over shape, dtype and raw bytes, streamed in row chunks.

    Chunk size is bounded in *bytes* (not rows) so the temporary copy stays
    flat as image width grows — this keeps digest computation out of the
    peak-memory budget for wide images.
    """
    h = hashlib.sha256()
    h.update(repr(tuple(arr.shape)).encode())
    h.update(str(arr.dtype).encode())
    row_bytes = max(1, int(np.prod(arr.shape[1:])) * arr.dtype.itemsize)
    chunk_rows = max(1, chunk_bytes // row_bytes)
    for r0 in range(0, arr.shape[0], chunk_rows):
        h.update(np.ascontiguousarray(arr[r0 : r0 + chunk_rows]).tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class KernelSpec:
    """Dense 2-D kernel or separable pair of 1-D kernels, with explicit anchors."""

    kind: str  # "dense" | "separable"
    weights: Optional[Tuple[Tuple[float, ...], ...]] = None  # dense, row-major
    anchor: Optional[Tuple[int, int]] = None
    col_weights: Optional[Tuple[float, ...]] = None  # separable, axis 0
    row_weights: Optional[Tuple[float, ...]] = None  # separable, axis 1
    col_anchor: Optional[int] = None
    row_anchor: Optional[int] = None

    # ------------------------------------------------------------------ ctor
    @classmethod
    def dense(cls, weights: Sequence[Sequence[float]], anchor: Optional[Tuple[int, int]] = None) -> "KernelSpec":
        arr = np.asarray(weights, dtype=np.float64)
        if arr.ndim != 2 or min(arr.shape) < 1:
            raise InvalidSpecError("dense kernel must be a non-empty 2-D array",
                                   {"shape": list(arr.shape)})
        if not np.all(np.isfinite(arr)):
            raise InvalidSpecError("dense kernel contains non-finite values")
        if anchor is None:
            anchor = (default_anchor(arr.shape[0]), default_anchor(arr.shape[1]))
        ar, ac = int(anchor[0]), int(anchor[1])
        if not (0 <= ar < arr.shape[0]) or not (0 <= ac < arr.shape[1]):
            raise InvalidSpecError("dense kernel anchor out of range",
                                   {"anchor": [ar, ac], "shape": list(arr.shape)})
        w = tuple(tuple(float(x) for x in row) for row in arr.tolist())
        return cls(kind="dense", weights=w, anchor=(ar, ac))

    @classmethod
    def separable(cls, col_weights: Sequence[float], row_weights: Sequence[float],
                  col_anchor: Optional[int] = None, row_anchor: Optional[int] = None) -> "KernelSpec":
        col, ca = _validate_1d(col_weights, col_anchor, "column")
        row, ra = _validate_1d(row_weights, row_anchor, "row")
        return cls(kind="separable", col_weights=col, row_weights=row,
                   col_anchor=ca, row_anchor=ra)

    # -------------------------------------------------------------- accessors
    def _require_dense(self) -> np.ndarray:
        if self.kind != "dense" or self.weights is None:
            raise InvalidSpecError("kernel is not dense", {"kind": self.kind})
        return np.asarray(self.weights, dtype=np.float64)

    def col_array(self) -> np.ndarray:
        if self.kind != "separable" or self.col_weights is None:
            raise InvalidSpecError("kernel is not separable", {"kind": self.kind})
        return np.asarray(self.col_weights, dtype=np.float64)

    def row_array(self) -> np.ndarray:
        if self.kind != "separable" or self.row_weights is None:
            raise InvalidSpecError("kernel is not separable", {"kind": self.kind})
        return np.asarray(self.row_weights, dtype=np.float64)

    def dense_weights(self) -> np.ndarray:
        """Resolved 2-D kernel (outer product col x row for separable specs)."""
        if self.kind == "dense":
            return self._require_dense()
        return np.outer(self.col_array(), self.row_array())

    def anchors_rc(self) -> Tuple[int, int]:
        if self.kind == "dense":
            assert self.anchor is not None
            return self.anchor
        assert self.col_anchor is not None and self.row_anchor is not None
        return (self.col_anchor, self.row_anchor)

    def halo(self) -> Tuple[int, int, int, int]:
        """(top, bottom, left, right) context required around a valid region.

        Convolution form: an axis of size ``n`` with anchor ``a`` needs
        ``n - 1 - a`` samples before and ``a`` samples after the region.
        """
        ar, ac = self.anchors_rc()
        kh, kw = self.dense_weights().shape
        return (kh - 1 - ar, ar, kw - 1 - ac, ac)

    # ---------------------------------------------------------------- digest
    def digest(self) -> str:
        h = hashlib.sha256()
        h.update(self.kind.encode())
        if self.kind == "dense":
            w = self._require_dense()
            h.update(repr(w.shape).encode())
            h.update(np.ascontiguousarray(w).tobytes())
            h.update(repr(self.anchor).encode())
        else:
            for arr, anch in ((self.col_array(), self.col_anchor),
                              (self.row_array(), self.row_anchor)):
                h.update(repr(arr.shape).encode())
                h.update(np.ascontiguousarray(arr).tobytes())
                h.update(repr(anch).encode())
        return h.hexdigest()

    # ---------------------------------------------------------- serialization
    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "weights": [list(r) for r in self.weights] if self.weights else None,
            "anchor": list(self.anchor) if self.anchor else None,
            "col_weights": list(self.col_weights) if self.col_weights else None,
            "row_weights": list(self.row_weights) if self.row_weights else None,
            "col_anchor": self.col_anchor,
            "row_anchor": self.row_anchor,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KernelSpec":
        kind = d.get("kind")
        if kind == "dense":
            return cls.dense(d["weights"], tuple(d["anchor"]) if d.get("anchor") else None)
        if kind == "separable":
            return cls.separable(d["col_weights"], d["row_weights"],
                                 d.get("col_anchor"), d.get("row_anchor"))
        raise InvalidSpecError("unknown kernel kind", {"kind": kind})


@dataclass(frozen=True)
class ImageSpec:
    """Contract for a stored image: shape, dtype, location and content digest."""

    image_id: str
    shape: Tuple[int, int]
    dtype: str
    path: str
    digest: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "shape": list(self.shape),
            "dtype": self.dtype,
            "path": self.path,
            "digest": self.digest,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImageSpec":
        return cls(
            image_id=d["image_id"],
            shape=(int(d["shape"][0]), int(d["shape"][1])),
            dtype=d["dtype"],
            path=d["path"],
            digest=d["digest"],
            meta=d.get("meta", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "ImageSpec":
        return cls.from_dict(json.loads(text))
