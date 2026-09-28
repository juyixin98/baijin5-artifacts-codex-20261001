"""Independent NumPy oracle.

Everything in this module computes expected results with NumPy *only*. It
must never import :mod:`tensorcraft.tensor`: the whole point is that the
reference answers are produced independently of the system under test.

Copy/alias answers come from observable NumPy signals (``owndata``,
``np.shares_memory``, data pointers) rather than from any core flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class RefValue:
    """An oracle-side value plus its observable memory facts."""

    array: np.ndarray
    copied_from: tuple[int, ...]   # input oracle ids that were NOT aliased
    aliased_inputs: tuple[int, ...]

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self.array.shape)

    @property
    def strides_elements(self) -> tuple[int, ...]:
        if self.array.dtype.itemsize == 0:  # pragma: no cover - numeric dtypes only
            return tuple(self.array.strides)
        return tuple(s // self.array.dtype.itemsize for s in self.array.strides)


def ref_from_nested(data: Any, dtype: str) -> np.ndarray:
    return np.asarray(data, dtype=np.dtype(dtype))


def ref_transpose(array: np.ndarray,
                  axes: Sequence[int] | None) -> np.ndarray:
    if axes is None:
        return array.T
    return np.transpose(array, tuple(axes))


def ref_reshape(array: np.ndarray, shape: Sequence[int],
                order: str) -> tuple[np.ndarray, bool]:
    """Reshape via NumPy; return ``(result, copied)`` from observable facts."""
    result = np.reshape(array, tuple(shape), order=order)
    copied = _numpy_copied(array, result)
    return result, copied


def ref_slice(array: np.ndarray, indexer: tuple) -> np.ndarray:
    return array[indexer]


def ref_broadcast_to(array: np.ndarray,
                     shape: Sequence[int]) -> np.ndarray:
    return np.broadcast_to(array, tuple(shape))


def ref_materialize(array: np.ndarray, order: str) -> np.ndarray:
    return np.array(array, copy=True, order=order)


_BINARY = {
    "add": np.add,
    "subtract": np.subtract,
    "multiply": np.multiply,
    "divide": np.true_divide,
    "floor_divide": np.floor_divide,
    "mod": np.mod,
    "power": np.power,
}


def ref_binary(left: np.ndarray, right: np.ndarray, op: str) -> np.ndarray:
    fn = _BINARY[op]
    if op == "divide" and left.dtype.kind in ("i", "u"):
        left = left.astype(np.float64)
        right = right.astype(np.float64)
    return fn(left, right)


def ref_scalar(array: np.ndarray, value: Any, op: str) -> np.ndarray:
    return _BINARY[op](array, np.asarray(value, dtype=array.dtype))


def ref_reduce_sum(array: np.ndarray, axis: int | None,
                   keepdims: bool) -> np.ndarray:
    return np.sum(array, axis=axis, keepdims=keepdims)


def ref_as_strided(data: Any, dtype: str, shape: Sequence[int],
                   strides: Sequence[int], offset: int = 0) -> np.ndarray:
    """Independent construction of an arbitrary-stride view (as_strided).

    Used to synthesize overlapping fixtures the same way a user would with
    plain NumPy -- ``strides`` are in *elements*. The storage offset is
    applied by advancing the base array before attaching strides.
    """
    base = np.asarray(data, dtype=np.dtype(dtype))
    if offset:
        base = base[offset:]
    itemsize = base.dtype.itemsize
    return np.lib.stride_tricks.as_strided(
        base, shape=tuple(shape),
        strides=tuple(s * itemsize for s in strides))


def _numpy_copied(before: np.ndarray, after: np.ndarray) -> bool:
    """Decide copy vs view from NumPy-observable memory facts.

    ``owndata`` is True exactly for freshly allocated buffers; a sharing
    relationship confirms a view. Conflicting signals are reported as a
    copy conservatively (callers surface the uncertainty separately).
    """
    if after.flags.owndata:
        return True
    return not np.shares_memory(before, after)


def arrays_match(left: np.ndarray, right: np.ndarray, *,
                 exact_dtype: bool = True) -> tuple[bool, str | None]:
    """Value comparison with dtype-appropriate strictness."""
    if left.shape != right.shape:
        return False, f"shape mismatch: {left.shape} vs {right.shape}"
    if exact_dtype and left.dtype != right.dtype:
        return False, f"dtype mismatch: {left.dtype} vs {right.dtype}"
    if left.dtype.kind == "f":
        if not np.allclose(left, right, rtol=1e-6, atol=1e-6,
                           equal_nan=True):
            return False, "float values differ beyond tolerance"
        return True, None
    if not np.array_equal(left, right):
        return False, f"values differ: {left.ravel()[:8].tolist()} vs {right.ravel()[:8].tolist()}"
    return True, None


def data_pointer(array: np.ndarray) -> tuple[int, int]:
    """``(start_address, nbytes)`` for uncertainty diagnostics."""
    return array.__array_interface__["data"][0], array.nbytes
