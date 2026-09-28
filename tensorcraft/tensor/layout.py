"""Stride layout: shape / strides / storage offset.

A :class:`Layout` is a pure description of how a 1-D buffer is addressed:

    offset(i_0, ..., i_{n-1}) = storage_offset + sum(i_k * stride_k)

Strides and offsets are counted in **elements**, not bytes (the dtype's
itemsize is applied only when bridging to NumPy).  Negative strides are
first-class (reversed views), zero strides mean a broadcast axis, and the
zero-size case follows NumPy's conventions (empty layouts address nothing
and therefore impose no region constraint).

The zero-copy reshape decision is a direct port of NumPy 2.4.6's
``_attempt_nocopy_reshape`` (numpy/_core/src/multiarray/shape.c), and the
C/F contiguity flags port ``_UpdateContiguousFlags``
(numpy/_core/src/multiarray/flagsobject.c).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Sequence

from ..errors import (
    BroadcastError,
    IndexOutOfBoundsError,
    InvalidStrideError,
    NonContiguousViewError,
    OverflowErrorCore,
    ShapeMismatchError,
    SizeMismatchError,
)

# NumPy uses NPY_INTP / Py_ssize_t; on the supported 64-bit platforms that
# is 2**63-1. Every product/offset is checked against this before access.
ADDRESS_LIMIT = 2**63 - 1


class Order(str, Enum):
    C = "C"
    F = "F"

    @classmethod
    def parse(cls, value: "str | Order") -> "Order":
        if isinstance(value, Order):
            return value
        if not isinstance(value, str):
            raise ShapeMismatchError(f"order must be 'C' or 'F', got {value!r}")
        try:
            return cls(value.upper())
        except ValueError as exc:
            raise ShapeMismatchError(
                f"order must be 'C' or 'F', got {value!r}") from exc


def _as_dim_tuple(value: Sequence[int], *, name: str = "shape") -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ShapeMismatchError(f"{name} must be a sequence of integers")
    dims: list[int] = []
    for dim in value:
        if isinstance(dim, bool) or not isinstance(dim, int):
            raise ShapeMismatchError(f"{name} entries must be ints, got {dim!r}")
        if dim < 0:
            raise ShapeMismatchError(f"{name} entries must be >= 0, got {dim}")
        dims.append(dim)
    return tuple(dims)


def checked_size(shape: Sequence[int]) -> int:
    """Multiply dimensions, detecting overflow before it can wrap.

    Mirrors NumPy's sequential ``npy_mul_sizes_with_overflow``: the running
    product is checked at every multiplication.
    """
    product = 1
    for dim in _as_dim_tuple(shape):
        if dim != 0 and product > ADDRESS_LIMIT // dim:
            raise OverflowErrorCore(
                f"shape {tuple(shape)} exceeds the addressable element limit "
                f"{ADDRESS_LIMIT}",
                details={"shape": tuple(shape), "limit": ADDRESS_LIMIT})
        product *= dim
    return product


def c_strides(shape: Sequence[int]) -> tuple[int, ...]:
    dims = _as_dim_tuple(shape)
    strides: list[int] = [0] * len(dims)
    running = 1
    for k in range(len(dims) - 1, -1, -1):
        strides[k] = running
        running *= dims[k]
    return tuple(strides)


def f_strides(shape: Sequence[int]) -> tuple[int, ...]:
    dims = _as_dim_tuple(shape)
    strides: list[int] = [0] * len(dims)
    running = 1
    for k, dim in enumerate(dims):
        strides[k] = running
        running *= dim
    return tuple(strides)


def default_strides(shape: Sequence[int], order: Order) -> tuple[int, ...]:
    return c_strides(shape) if order is Order.C else f_strides(shape)


@dataclass(frozen=True)
class Layout:
    """Immutable (shape, strides, storage_offset) description."""

    shape: tuple[int, ...]
    strides: tuple[int, ...]
    storage_offset: int = 0

    def __post_init__(self) -> None:
        shape = _as_dim_tuple(self.shape)
        if not isinstance(self.strides, Sequence) or isinstance(
                self.strides, (str, bytes)):
            raise InvalidStrideError("strides must be a sequence of integers")
        if len(self.strides) != len(shape):
            raise InvalidStrideError(
                f"strides/shape rank mismatch: {len(self.strides)} strides "
                f"for shape {shape}")
        strides: list[int] = []
        for stride in self.strides:
            if isinstance(stride, bool) or not isinstance(stride, int):
                raise InvalidStrideError(
                    f"stride entries must be ints, got {stride!r}")
            strides.append(stride)
        if isinstance(self.storage_offset, bool) or not isinstance(
                self.storage_offset, int):
            raise InvalidStrideError("storage_offset must be an int")
        # Overflow check on the dimension product before any access can happen.
        checked_size(shape)
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "strides", tuple(strides))

    # -- basic attributes --------------------------------------------------

    @property
    def ndim(self) -> int:
        return len(self.shape)

    @property
    def size(self) -> int:
        product = 1
        for dim in self.shape:
            product *= dim
        return product

    def region_bounds(self) -> tuple[int, int]:
        """Lowest/highest *relative* element offset the layout addresses."""
        lo = hi = 0
        for dim, stride in zip(self.shape, self.strides):
            if dim == 0:
                return 0, 0
            if dim == 1:
                continue
            if stride >= 0:
                hi += (dim - 1) * stride
            else:
                lo += (dim - 1) * stride
        return lo, hi

    def validate_for_buffer(self, buffer_length: int) -> None:
        """Raise if any addressed element lies outside the backing buffer."""
        if self.size == 0:
            # Empty arrays address no element; NumPy keeps arbitrary
            # strides/offsets on them (e.g. empty reversed slices).
            return
        lo, hi = self.region_bounds()
        start = self.storage_offset + lo
        end = self.storage_offset + hi
        if start < 0 or end >= buffer_length:
            raise InvalidStrideError(
                f"layout addresses elements [{start}, {end}] but buffer has "
                f"{buffer_length} elements",
                details={
                    "address_min": start,
                    "address_max": end,
                    "buffer_length": buffer_length,
                })

    # -- index addressing --------------------------------------------------

    @staticmethod
    def _normalize(index: int, dim: int, axis: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int):
            raise IndexOutOfBoundsError(
                f"index must be an int, got {index!r}",
                details={"axis": axis, "index": index, "size": dim})
        normalized = index + dim if index < 0 else index
        if not (0 <= normalized < dim):
            raise IndexOutOfBoundsError(
                f"index {index} is out of bounds for axis {axis} with size {dim}",
                details={"axis": axis, "index": index, "size": dim})
        return normalized

    def element_offset(self, indices: Sequence[int]) -> int:
        """Offset for a fully-specified multi-index; bounds checked first."""
        if len(indices) != self.ndim:
            raise ShapeMismatchError(
                f"expected {self.ndim} indices, got {len(indices)}")
        offset = self.storage_offset
        for axis, (index, dim, stride) in enumerate(
                zip(indices, self.shape, self.strides)):
            if dim == 0:
                raise IndexOutOfBoundsError(
                    "cannot index into a size-0 axis",
                    details={"axis": axis, "index": index, "size": 0})
            normalized = self._normalize(index, dim, axis)
            offset += normalized * stride
            if not -ADDRESS_LIMIT <= offset <= ADDRESS_LIMIT:
                raise OverflowErrorCore(
                    "computed element offset overflows the addressable range",
                    details={"offset": offset})
        return offset

    def iter_indices(self) -> Iterator[tuple[tuple[int, ...], int]]:
        """Yield ``(multi_index, absolute_offset)`` in C order."""
        if self.size == 0:
            return

        def recurse(axis: int, partial: list[int], offset: int):
            if axis == len(self.shape):
                yield tuple(partial), offset
                return
            dim, stride = self.shape[axis], self.strides[axis]
            for index in range(dim):
                partial.append(index)
                yield from recurse(axis + 1, partial, offset + index * stride)
                partial.pop()

        yield from recurse(0, [], self.storage_offset)

    # -- contiguity flags (port of _UpdateContiguousFlags) -----------------

    def is_c_contiguous(self) -> bool:
        expected = 1
        for dim, stride in zip(reversed(self.shape), reversed(self.strides)):
            if dim == 0:
                return True
            if dim != 1:
                if stride != expected:
                    return False
                expected *= dim
        return True

    def is_f_contiguous(self) -> bool:
        # NumPy marks *both* flags contiguous as soon as a zero-size axis
        # exists (the early return lives in its single C-order scan, which
        # runs first), so replicate that shared precondition here.
        if self.size == 0:
            return True
        expected = 1
        for dim, stride in zip(self.shape, self.strides):
            if dim != 1:
                if stride != expected:
                    return False
                expected *= dim
        return True


def broadcast_shape(*shapes: Sequence[int]) -> tuple[int, ...]:
    """NumPy broadcasting rules; raises :class:`BroadcastError`."""
    rank = max((len(shape) for shape in shapes), default=0)
    padded = [((1,) * (rank - len(shape)) + tuple(shape)) for shape in shapes]
    result: list[int] = []
    for axis in range(rank):
        dims = {shape[axis] for shape in padded}
        dims.discard(1)
        if len(dims) > 1:
            raise BroadcastError(
                f"shapes cannot be broadcast together: "
                f"{[tuple(s) for s in shapes]}",
                details={"shapes": [tuple(s) for s in shapes], "axis": axis})
        result.append(next(iter(dims)) if dims else 1)
    return tuple(result)


def broadcast_strides(
    shape: Sequence[int],
    strides: Sequence[int],
    target: Sequence[int],
) -> tuple[int, ...]:
    """Strides that broadcast ``(shape, strides)`` into ``target``.

    Prepended dimensions and length-1 input axes get stride 0, matching
    ``np.broadcast_to``.
    """
    if len(target) < len(shape):
        raise BroadcastError(
            f"cannot broadcast rank {len(shape)} into rank {len(target)}",
            details={"shape": tuple(shape), "target": tuple(target)})
    pad = len(target) - len(shape)
    out = [0] * pad
    for axis, (dim, stride, target_dim) in enumerate(
            zip(shape, strides, target[pad:])):
        if dim == target_dim:
            out.append(stride)
        elif dim == 1:
            out.append(0)
        else:
            raise BroadcastError(
                f"axis size {dim} cannot be broadcast to {target_dim}",
                details={
                    "shape": tuple(shape),
                    "target": tuple(target),
                    "axis": pad + axis,
                })
    return tuple(out)


def infer_unknown_dimension(
    shape: Sequence[int], total_size: int
) -> tuple[int, ...]:
    """Resolve at most one ``-1`` entry; port of ``_fix_unknown_dimension``."""
    dims = list(shape)
    unknown = -1
    known_product = 1
    for axis, dim in enumerate(dims):
        if isinstance(dim, bool) or not isinstance(dim, int):
            raise SizeMismatchError(f"shape entries must be ints, got {dim!r}")
        if dim < -1:
            raise SizeMismatchError(f"shape entries must be >= -1, got {dim}")
        if dim == -1:
            if unknown != -1:
                raise SizeMismatchError(
                    "can only specify one unknown dimension (-1)",
                    details={"shape": tuple(shape)})
            unknown = axis
        else:
            if dim != 0 and known_product > ADDRESS_LIMIT // dim:
                raise OverflowErrorCore(
                    "shape product overflow during -1 inference",
                    details={"shape": tuple(shape)})
            known_product *= dim
    if unknown != -1:
        if known_product == 0 or total_size % known_product != 0:
            raise SizeMismatchError(
                f"cannot reshape array of size {total_size} into shape "
                f"{tuple(shape)}",
                details={"size": total_size, "shape": tuple(shape)})
        dims[unknown] = total_size // known_product
    elif known_product != total_size:
        raise SizeMismatchError(
            f"cannot reshape array of size {total_size} into shape {tuple(shape)}",
            details={"size": total_size, "shape": tuple(shape)})
    return tuple(dims)


def try_nocopy_reshape(
    shape: Sequence[int],
    strides: Sequence[int],
    new_shape: Sequence[int],
    order: Order,
) -> tuple[int, ...] | None:
    """Port of NumPy's ``_attempt_nocopy_reshape`` (element strides).

    Precondition (as in NumPy): ``new_shape`` is size-compatible with
    ``shape`` and contains no ``-1``. Returns new strides when a view
    suffices, or ``None`` when a copy is unavoidable for this order.
    """
    compact_dims: list[int] = []
    compact_strides: list[int] = []
    for dim, stride in zip(shape, strides):
        if dim != 1:  # length-1 axes carry no layout information
            compact_dims.append(dim)
            compact_strides.append(stride)

    new_dims = list(new_shape)
    newnd = len(new_dims)
    new_strides = [0] * newnd
    oldnd = len(compact_dims)

    oi = 0
    oj = 1
    ni = 0
    nj = 1
    while ni < newnd and oi < oldnd:
        new_product = new_dims[ni]
        old_product = compact_dims[oi]

        while new_product != old_product:
            if new_product < old_product:
                new_product *= new_dims[nj]
                nj += 1
            else:
                old_product *= compact_dims[oj]
                oj += 1

        # The original axes consumed here must be mutually contiguous.
        for ok in range(oi, oj - 1):
            if order is Order.F:
                if compact_strides[ok + 1] != compact_dims[ok] * compact_strides[ok]:
                    return None
            elif compact_strides[ok] != compact_dims[ok + 1] * compact_strides[ok + 1]:
                return None

        if order is Order.F:
            new_strides[ni] = compact_strides[oi]
            for nk in range(ni + 1, nj):
                new_strides[nk] = new_strides[nk - 1] * new_dims[nk - 1]
        else:
            new_strides[nj - 1] = compact_strides[oj - 1]
            for nk in range(nj - 1, ni, -1):
                new_strides[nk - 1] = new_strides[nk] * new_dims[nk]

        ni = nj
        nj += 1
        oi = oj
        oj += 1

    # Trailing new length-1 axes: arbitrary strides; NumPy hands them the
    # stride of the next-fastest index (itemsize when nothing was consumed).
    if ni >= 1:
        last_stride = new_strides[ni - 1]
        if order is Order.F:
            last_stride *= new_dims[ni - 1]
    else:
        last_stride = 1  # element-stride version of NumPy's itemsize
    for nk in range(ni, newnd):
        new_strides[nk] = last_stride

    return tuple(new_strides)


@dataclass(frozen=True)
class ReshapePlan:
    layout: Layout
    copied: bool
    reason: str


def reshape_plan(
    layout: Layout,
    requested_shape: Sequence[int],
    order: "str | Order",
    *,
    copy_allowed: bool = True,
) -> ReshapePlan:
    """Decide whether a reshape is a zero-copy view or requires a copy.

    Rules, matching ``ndarray.reshape``:

    * same shape -> the exact same layout (view alias);
    * empty layout -> canonical strides (NumPy flags empty arrays both
      C- and F-contiguous);
    * contiguous in the requested order -> canonical strides, view;
    * otherwise run the NumPy no-copy attempt (it can still succeed on
      non-contiguous layouts);
    * on failure either copy or raise :class:`NonContiguousViewError`
      when ``copy_allowed`` is false.
    """
    order = Order.parse(order)
    target = infer_unknown_dimension(requested_shape, layout.size)

    if target == tuple(layout.shape):
        return ReshapePlan(layout, copied=False, reason="shape_unchanged")

    if layout.size == 0:
        return ReshapePlan(
            Layout(target, default_strides(target, order), layout.storage_offset),
            copied=False,
            reason="empty_layout",
        )

    already = (
        layout.is_c_contiguous() if order is Order.C else layout.is_f_contiguous()
    )
    if already:
        return ReshapePlan(
            Layout(target, default_strides(target, order), layout.storage_offset),
            copied=False,
            reason="contiguous_in_requested_order",
        )

    candidate = try_nocopy_reshape(layout.shape, layout.strides, target, order)
    if candidate is not None:
        return ReshapePlan(
            Layout(target, candidate, layout.storage_offset),
            copied=False,
            reason="nocopy_attempt_succeeded",
        )

    if not copy_allowed:
        raise NonContiguousViewError(
            f"reshape {layout.shape} -> {target} (order {order.value}) requires "
            "a copy but copies were forbidden",
            details={"shape": layout.shape, "target": target, "order": order.value},
        )
    return ReshapePlan(
        Layout(target, default_strides(target, order), 0),
        copied=True,
        reason="non_contiguous_materialized",
    )
