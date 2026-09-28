"""Basic indexer parsing: ints, slices, ``Ellipsis`` and ``np.newaxis``.

The parser is a *pure layout transform* -- given an input layout and an
indexer it computes the output ``(shape, strides, offset)`` without
touching element data, so the exact same rules govern reads and writes.

Supported indexer entries:

    int        removes the axis; negative values are normalized; OOB rejected
    slice      standard Python slicing including negative steps (``step=0``
               is rejected); endpoints are clamped like ``slice.indices``
    Ellipsis   expands to enough full slices to cover remaining axes
    None       insert a length-1 broadcast axis with stride 0

Boolean scalars and fancy (array/list) indexing are explicitly rejected:
boolean/fancy indexing has copy/alias semantics that deserve a separate,
explicit feature rather than being silently accepted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ..errors import (
    IndexOutOfBoundsError,
    InvalidIndexError,
    ShapeMismatchError,
)

# Public sentinel matching ``np.newaxis``.
newaxis = None


@dataclass(frozen=True)
class AxisStep:
    """Explainable record of how one indexer entry was resolved."""

    kind: str                  # "int" | "slice" | "newaxis"
    source_axis: int | None    # input axis consumed, None for newaxis
    normalized_index: int | None = None
    start: int | None = None
    stop: int | None = None
    step: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class IndexPlan:
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    storage_offset: int
    steps: tuple[AxisStep, ...]


def _normalize_int(value, *, axis: int, dim: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidIndexError(
            f"index on axis {axis} must be an int, got {value!r}",
            details={"axis": axis, "value": repr(value)})
    normalized = value + dim if value < 0 else value
    if not 0 <= normalized < dim:
        raise IndexOutOfBoundsError(
            f"index {value} is out of bounds for axis {axis} with size {dim}",
            details={"axis": axis, "index": value, "size": dim})
    return normalized


def _normalize_slice(raw: slice, *, axis: int, dim: int) -> tuple[int, int, int, int]:
    """Return ``(start, stop, step, count)`` using CPython's own clamping."""
    step = 1 if raw.step is None else raw.step
    if isinstance(step, bool) or not isinstance(step, int):
        raise InvalidIndexError(
            f"slice step on axis {axis} must be an int, got {step!r}",
            details={"axis": axis, "value": repr(step)})
    if step == 0:
        raise InvalidIndexError(
            f"slice step cannot be zero (axis {axis})",
            details={"axis": axis})
    for name, endpoint in (("start", raw.start), ("stop", raw.stop)):
        if endpoint is not None and (
                isinstance(endpoint, bool) or not isinstance(endpoint, int)):
            raise InvalidIndexError(
                f"slice {name} on axis {axis} must be an int or None, got "
                f"{endpoint!r}",
                details={"axis": axis, "name": name, "value": repr(endpoint)})
    # slice.indices is the audited CPython implementation of negative-index
    # normalization + endpoint clamping for both step directions.
    start, stop, step = raw.indices(dim)
    count = max(0, math.ceil((stop - start) / step))
    return start, stop, step, count


def _expand_entries(indexer, ndim: int) -> tuple[list, ...]:
    if not isinstance(indexer, tuple):
        indexer = (indexer,)

    if sum(1 for entry in indexer if entry is Ellipsis) > 1:
        raise InvalidIndexError("an index may only contain a single Ellipsis (...)")

    expanded: list = []
    for entry in indexer:
        if entry is Ellipsis:
            consumed = sum(
                1 for other in indexer
                if other is not Ellipsis and other is not None)
            fill = ndim - consumed
            if fill < 0:
                raise ShapeMismatchError(
                    f"too many indices for tensor of dimension {ndim}")
            expanded.extend([slice(None)] * max(0, fill))
        else:
            expanded.append(entry)

    consuming = sum(1 for entry in expanded if entry is not None)
    if consuming > ndim:
        raise ShapeMismatchError(
            f"too many indices for tensor of dimension {ndim}: got {consuming}")
    # Omitted trailing axes mean full slices (x[0] == x[0, :]).
    expanded.extend([slice(None)] * (ndim - consuming))
    return expanded


def parse_indexer(
    indexer,
    shape: Sequence[int],
    strides: Sequence[int],
    storage_offset: int = 0,
) -> IndexPlan:
    """Translate an indexer into an output layout plus explainable steps."""
    entries = _expand_entries(indexer, len(shape))

    out_shape: list[int] = []
    out_strides: list[int] = []
    steps: list[AxisStep] = []
    offset = storage_offset
    input_axis = 0

    for entry in entries:
        if entry is None:
            out_shape.append(1)
            out_strides.append(0)
            steps.append(AxisStep(kind="newaxis", source_axis=None))
        elif isinstance(entry, slice):
            dim = shape[input_axis]
            start, stop, step, count = _normalize_slice(
                entry, axis=input_axis, dim=dim)
            out_shape.append(count)
            out_strides.append(step * strides[input_axis])
            offset += start * strides[input_axis]
            steps.append(AxisStep(
                kind="slice", source_axis=input_axis,
                start=start, stop=stop, step=step, count=count))
            input_axis += 1
        elif isinstance(entry, int) and not isinstance(entry, bool):
            dim = shape[input_axis]
            normalized = _normalize_int(entry, axis=input_axis, dim=dim)
            offset += normalized * strides[input_axis]
            steps.append(AxisStep(
                kind="int", source_axis=input_axis,
                normalized_index=normalized))
            input_axis += 1
        else:
            raise InvalidIndexError(
                f"unsupported indexer entry {entry!r}; supported forms are "
                "int, slice, Ellipsis and None (fancy/boolean indexing is "
                "not supported)",
                details={"axis": input_axis, "value": repr(entry)})

    return IndexPlan(
        shape=tuple(out_shape),
        strides=tuple(out_strides),
        storage_offset=offset,
        steps=tuple(steps),
    )
