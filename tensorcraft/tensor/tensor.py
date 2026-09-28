"""The :class:`Tensor` type: storage + layout + views + reads/writes.

A tensor is a ``(Storage, Layout)`` pair. Every view operation returns a
new tensor over the *same* storage; only explicit materialization allocates
fresh memory. The class therefore answers three questions precisely:

* which element does a position address  (the :class:`Layout`);
* do two tensors alias storage           (``shares_storage_with``);
* does a write observe an overlap        (``is_self_overlapping`` /
  ``overlaps``), and is it rejected or buffered deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..errors import (
    BroadcastError,
    InvalidStrideError,
    OverlapWriteError,
    ShapeMismatchError,
    UnsupportedDTypeError,
)
from .dtypes import DTypeInfo, resolve_dtype
from .indexing import parse_indexer
from .layout import (
    Layout,
    Order,
    broadcast_shape,
    broadcast_strides,
    c_strides,
    default_strides,
    reshape_plan,
)
from .storage import Storage


@dataclass(frozen=True)
class WriteReport:
    """Explainable record of a write, including overlap handling."""

    elements_written: int
    buffered_source: bool
    temp_copy: bool
    policy: str


class Tensor:
    """Stride-layout tensor over a shared 1-D storage."""

    __slots__ = ("storage", "layout", "_history")

    def __init__(self, storage: Storage, layout: Layout,
                 *, history: str | None = None) -> None:
        if not isinstance(storage, Storage):
            raise TypeError("storage must be a Storage instance")
        if not isinstance(layout, Layout):
            raise TypeError("layout must be a Layout instance")
        layout.validate_for_buffer(storage.length)
        self.storage = storage
        self.layout = layout
        self._history = history or "allocated"

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    @classmethod
    def from_nested(cls, data: Any, dtype: str | DTypeInfo | None = None,
                    *, history: str = "from_nested") -> "Tensor":
        if isinstance(data, Tensor):
            if dtype is not None and resolve_dtype(dtype).name != data.dtype.name:
                return data.astype(resolve_dtype(dtype))
            return data
        try:
            array = np.asarray(data)
        except Exception as exc:  # NumPy raises a variety of ValueErrors
            raise ShapeMismatchError(
                f"cannot interpret payload as a tensor: {exc}") from exc
        if array.dtype.kind not in ("i", "u", "f"):
            raise UnsupportedDTypeError(
                f"payload dtype {array.dtype!r} is not numeric")
        info = resolve_dtype(dtype) if dtype is not None else resolve_dtype(array.dtype)
        if array.dtype != info.np_dtype:
            array = array.astype(info.np_dtype)
        # np.array(..., copy=True, order="C") yields a compact C buffer while
        # preserving rank (np.ascontiguousarray would promote a 0-D scalar
        # to shape (1,)).
        array = np.array(array, copy=True, order="C")
        shape = tuple(array.shape)
        storage = Storage(array.reshape(-1), info)
        layout = Layout(shape, c_strides(shape), 0)
        return cls(storage, layout, history=history)

    @classmethod
    def zeros(cls, shape: Sequence[int], dtype: str | DTypeInfo = "float64",
              *, order: "str | Order" = Order.C) -> "Tensor":
        info = resolve_dtype(dtype)
        dims = tuple(int(d) for d in shape)
        storage = Storage.allocate(cls._product(dims), info)
        return cls(storage, Layout(dims, default_strides(dims, Order.parse(order)), 0),
                   history=f"zeros(order={Order.parse(order).value})")

    @classmethod
    def arange(cls, start_or_stop: int, stop: int | None = None,
               step: int = 1, dtype: str | DTypeInfo | None = None) -> "Tensor":
        if step == 0:
            raise InvalidStrideError("arange step cannot be zero")
        if stop is None:
            start, stop = 0, start_or_stop
        else:
            start = start_or_stop
        values = np.arange(start, stop, step)
        info = resolve_dtype(dtype) if dtype is not None \
            else resolve_dtype(values.dtype)
        flat = values.astype(info.np_dtype, copy=False)
        storage = Storage(np.ascontiguousarray(flat).reshape(-1), info)
        return cls(storage, Layout((int(values.size),), (1,), 0), history="arange")

    @staticmethod
    def _product(shape: Sequence[int]) -> int:
        product = 1
        for dim in shape:
            product *= int(dim)
        return product

    # ------------------------------------------------------------------ #
    # Basic attributes
    # ------------------------------------------------------------------ #

    @property
    def dtype(self) -> DTypeInfo:
        return self.storage.dtype

    @property
    def shape(self) -> tuple[int, ...]:
        return self.layout.shape

    @property
    def strides(self) -> tuple[int, ...]:
        return self.layout.strides

    @property
    def storage_offset(self) -> int:
        return self.layout.storage_offset

    @property
    def ndim(self) -> int:
        return self.layout.ndim

    @property
    def size(self) -> int:
        return self.layout.size

    @property
    def token(self) -> int:
        return self.storage.token

    @property
    def history(self) -> str:
        return self._history

    def is_c_contiguous(self) -> bool:
        return self.layout.is_c_contiguous()

    def is_f_contiguous(self) -> bool:
        return self.layout.is_f_contiguous()

    # ------------------------------------------------------------------ #
    # NumPy bridge (independent value extraction through the layout)
    # ------------------------------------------------------------------ #

    def numpy_view(self) -> np.ndarray:
        """Zero-copy NumPy view with exactly this shape/strides/offset."""
        itemsize = self.dtype.itemsize
        return np.ndarray(
            shape=self.shape,
            dtype=self.dtype.np_dtype,
            buffer=self.storage.buffer,
            offset=self.storage_offset * itemsize,
            strides=tuple(s * itemsize for s in self.strides),
        )

    def to_numpy(self) -> np.ndarray:
        """Contiguous materialized copy of the values (C order)."""
        return np.array(self.numpy_view(), copy=True, order="C")

    def to_nested(self) -> Any:
        return self.to_numpy().tolist()

    # ------------------------------------------------------------------ #
    # Aliasing / overlap
    # ------------------------------------------------------------------ #

    def shares_storage_with(self, other: "Tensor") -> bool:
        return isinstance(other, Tensor) and self.storage is other.storage

    def addressed_offsets(self) -> tuple[int, ...]:
        """Absolute storage offsets addressed, in C position order."""
        return tuple(
            offset for _, offset in self.layout.iter_indices()
        )

    def is_self_overlapping(self) -> bool:
        """True when distinct positions map to the same storage element."""
        offsets = self.addressed_offsets()
        return len(set(offsets)) != len(offsets)

    def overlaps(self, other: "Tensor") -> bool:
        """True when the two tensors address any common storage element."""
        if not self.shares_storage_with(other):
            return False
        return bool(set(self.addressed_offsets())
                    & set(other.addressed_offsets()))

    def _as_view(self, layout: Layout, history: str) -> "Tensor":
        return Tensor(self.storage, layout, history=history)

    # ------------------------------------------------------------------ #
    # View operations (zero copy)
    # ------------------------------------------------------------------ #

    def transpose(self, axes: Sequence[int] | None = None) -> "Tensor":
        if axes is None:
            new_axes = tuple(range(self.ndim - 1, -1, -1))
        else:
            new_axes = tuple(axes)
            if len(new_axes) != self.ndim:
                raise ShapeMismatchError(
                    f"transpose expects {self.ndim} axes, got {len(new_axes)}")
            if sorted(new_axes) != list(range(self.ndim)):
                raise ShapeMismatchError(
                    f"transpose axes {new_axes} must be a permutation of "
                    f"range({self.ndim})")
        new_shape = tuple(self.shape[a] for a in new_axes)
        new_strides = tuple(self.strides[a] for a in new_axes)
        return self._as_view(
            Layout(new_shape, new_strides, self.storage_offset),
            f"transpose{new_axes}")

    @property
    def T(self) -> "Tensor":
        return self.transpose()

    def swapaxes(self, axis1: int, axis2: int) -> "Tensor":
        axes = list(range(self.ndim))
        axes[axis1], axes[axis2] = axes[axis2], axes[axis1]
        return self.transpose(axes)

    def getitem(self, indexer: Any) -> "Tensor":
        plan = parse_indexer(
            indexer, self.shape, self.strides, self.storage_offset)
        return self._as_view(
            Layout(plan.shape, plan.strides, plan.storage_offset),
            f"getitem({_describe_indexer(indexer)})")

    def __getitem__(self, indexer: Any) -> "Tensor":
        return self.getitem(indexer)

    def squeeze(self, axis: int | None = None) -> "Tensor":
        if axis is None:
            axes = [ax for ax, dim in enumerate(self.shape) if dim == 1]
        else:
            axes = [axis]
            if not 0 <= axis < self.ndim:
                raise ShapeMismatchError(f"squeeze axis {axis} out of range")
            if self.shape[axis] != 1:
                raise ShapeMismatchError(
                    f"cannot squeeze axis {axis} with size {self.shape[axis]}")
        kept = [ax for ax in range(self.ndim) if ax not in axes]
        new_shape = tuple(self.shape[ax] for ax in kept)
        new_strides = tuple(self.strides[ax] for ax in kept)
        return self._as_view(
            Layout(new_shape, new_strides, self.storage_offset), "squeeze")

    def broadcast_to(self, target_shape: Sequence[int]) -> "Tensor":
        target = tuple(int(d) for d in target_shape)
        new_strides = broadcast_strides(
            self.shape, self.strides, target)
        return self._as_view(
            Layout(target, new_strides, self.storage_offset),
            f"broadcast_to{target}")

    def reshape(
        self,
        new_shape: Sequence[int],
        order: "str | Order" = Order.C,
        *,
        allow_copy: bool = True,
    ) -> "Tensor":
        plan = reshape_plan(self.layout, new_shape, order,
                            copy_allowed=allow_copy)
        if not plan.copied:
            return self._as_view(plan.layout, f"reshape{plan.layout.shape}(view)")
        materialized = self.materialize(order)
        return Tensor(
            materialized.storage,
            Layout(plan.layout.shape,
                   default_strides(plan.layout.shape, Order.parse(order)), 0),
            history=f"reshape{plan.layout.shape}(copy:{plan.reason})")

    def materialize(self, order: "str | Order" = Order.C) -> "Tensor":
        """Return a fresh, contiguous, compact tensor (always a copy)."""
        order = Order.parse(order)
        # np.ravel with explicit order walks positions in C or F sequence.
        values = np.ravel(self.numpy_view(), order=order.value).copy()
        storage = Storage(values, self.dtype)
        layout = Layout(tuple(self.shape),
                        default_strides(self.shape, order), 0)
        return Tensor(storage, layout, history=f"materialize({order.value})")

    def astype(self, dtype: str | DTypeInfo) -> "Tensor":
        info = resolve_dtype(dtype)
        if info.name == self.dtype.name:
            return self
        values = self.to_numpy().astype(info.np_dtype).reshape(-1)
        return Tensor(Storage(values, info),
                      Layout(self.shape, c_strides(self.shape), 0),
                      history=f"astype({info.name})")

    # ------------------------------------------------------------------ #
    # Reads / writes
    # ------------------------------------------------------------------ #

    def gather(self) -> np.ndarray:
        """Values in C position order as a fresh 1-D NumPy array."""
        flat = np.empty(self.size, dtype=self.dtype.np_dtype)
        buffer = self.storage.buffer
        for position, (_, offset) in enumerate(self.layout.iter_indices()):
            flat[position] = buffer[offset]
        return flat

    def assign_scalar(self, value, *, policy: str = "reject") -> WriteReport:
        """Fill every addressed position, honoring the overlap policy."""
        scalar = np.asarray(value, dtype=self.dtype.np_dtype)
        offsets = self.addressed_offsets()
        used_temp = False
        if len(set(offsets)) != len(offsets):
            if policy == "reject":
                raise _overlap_error(self, offsets)
            # temp_copy policy: scatter in C order; last write wins, which is
            # deterministic and identical on every run.
            used_temp = True
        self.storage.buffer[list(offsets)] = scalar
        return WriteReport(elements_written=len(offsets),
                           buffered_source=False, temp_copy=used_temp,
                           policy=policy)

    def assign(self, source: "Tensor", *, policy: str = "reject") -> WriteReport:
        """Write the values of ``source`` (broadcast if needed) into self."""
        if not isinstance(source, Tensor):
            raise TypeError("assign source must be a Tensor")
        target_shape = broadcast_shape(self.shape, source.shape)
        if target_shape != self.shape:
            raise BroadcastError(
                f"cannot assign shape {source.shape} into {self.shape}",
                details={"target": self.shape, "source": source.shape})
        src = source if source.shape == self.shape \
            else source.broadcast_to(self.shape)

        dst_offsets = self.addressed_offsets()
        # Always gather source values first: when src and dst share storage,
        # this snapshot makes the write independent of scatter order.
        values = src.gather()
        buffered_source = src.shares_storage_with(self)

        used_temp = False
        if len(set(dst_offsets)) != len(dst_offsets):
            if policy == "reject":
                raise _overlap_error(self, dst_offsets)
            # Stage into a position-indexed temporary, then scatter back in
            # C order (last write wins deterministically).
            used_temp = True
            staged = self.gather()
            staged[:] = values
            self.storage.buffer[list(dst_offsets)] = staged
        else:
            self.storage.buffer[list(dst_offsets)] = values
        return WriteReport(elements_written=len(dst_offsets),
                           buffered_source=buffered_source,
                           temp_copy=used_temp, policy=policy)

    # ------------------------------------------------------------------ #
    # Comparison helpers
    # ------------------------------------------------------------------ #

    def values_equal(self, other: "Tensor") -> bool:
        if self.shape != other.shape:
            return False
        return np.array_equal(self.to_numpy(), other.to_numpy())

    def allclose(self, other: "Tensor", *, rtol: float = 1e-7,
                 atol: float = 1e-10) -> bool:
        if self.shape != other.shape:
            return False
        return np.allclose(self.to_numpy(), other.to_numpy(),
                           rtol=rtol, atol=atol)

    def describe(self) -> dict:
        """JSON-serializable layout/alias description for API responses."""
        return {
            "shape": list(self.shape),
            "strides": list(self.strides),
            "storage_offset": self.storage_offset,
            "ndim": self.ndim,
            "size": self.size,
            "dtype": self.dtype.name,
            "storage_token": self.token,
            "c_contiguous": self.is_c_contiguous(),
            "f_contiguous": self.is_f_contiguous(),
            "self_overlapping": self.is_self_overlapping() if self.size <= 4096
            else None,
            "history": self._history,
        }


def _describe_indexer(indexer: Any) -> str:
    if not isinstance(indexer, tuple):
        indexer = (indexer,)
    parts = []
    for entry in indexer:
        if entry is Ellipsis:
            parts.append("...")
        elif entry is None:
            parts.append("newaxis")
        elif isinstance(entry, slice):
            parts.append(f"{entry.start}:{entry.stop}:{entry.step}")
        else:
            parts.append(repr(entry))
    return ",".join(parts)


def _overlap_error(tensor: Tensor, offsets: Sequence[int]) -> OverlapWriteError:
    unique = len(set(offsets))
    seen: dict[int, int] = {}
    example = None
    for offset in offsets:
        if offset in seen:
            example = offset
            break
        seen[offset] = 1
    return OverlapWriteError(
        f"refused write into a self-overlapping view: {len(offsets)} "
        f"positions address only {unique} storage elements "
        f"(first duplicated offset {example})",
        details={
            "positions": len(offsets),
            "unique_elements": unique,
            "first_duplicated_offset": example,
            "shape": list(tensor.shape),
            "strides": list(tensor.strides),
        })
