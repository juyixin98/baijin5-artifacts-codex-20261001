"""One-dimensional owning storage plus shared-buffer bookkeeping.

A :class:`Storage` is a flat, contiguous, 1-D NumPy array.  Tensors never
own element memory themselves: they hold a reference to a storage plus a
:class:`~tensorcraft.tensor.layout.Layout`.  Views (slice, transpose,
zero-copy reshape, broadcast) pass the *same* Storage object, which makes
aliasing detectable both by ``is`` and by the stable ``token`` id.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from ..errors import OverflowErrorCore
from .dtypes import DTypeInfo

_TOKEN_COUNTER = itertools.count(1)


@dataclass(frozen=True)
class Storage:
    """Flat 1-D buffer. ``token`` is the stable alias identity."""

    buffer: np.ndarray
    dtype: DTypeInfo
    token: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.buffer, np.ndarray):
            raise TypeError("Storage requires a numpy.ndarray")
        if self.buffer.ndim != 1:
            raise ValueError("Storage buffer must be 1-D")
        if self.buffer.dtype != self.dtype.np_dtype:
            raise ValueError(
                f"buffer dtype {self.buffer.dtype} != {self.dtype.name}")
        if not self.buffer.flags["C_CONTIGUOUS"]:
            object.__setattr__(
                self, "buffer", np.ascontiguousarray(self.buffer))
        if self.token == 0:
            object.__setattr__(self, "token", next(_TOKEN_COUNTER))

    @property
    def length(self) -> int:
        return int(self.buffer.shape[0])

    @classmethod
    def allocate(cls, size: int, dtype: DTypeInfo) -> "Storage":
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise OverflowErrorCore(
                f"storage size must be a non-negative int, got {size!r}")
        return cls(np.zeros(size, dtype=dtype.np_dtype), dtype)

    @classmethod
    def from_flat(cls, values, dtype: DTypeInfo) -> "Storage":
        flat = np.asarray(values, dtype=dtype.np_dtype).reshape(-1)
        return cls(flat.copy(), dtype)
