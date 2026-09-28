"""Local synthetic verification scenarios.

Every scenario is data-only and executes on both the core interpreter and
the independent NumPy oracle. The four families demanded by the contract
are all present:

1. transpose then reshape (the classic copy-vs-view decision);
2. broadcasting with zero strides;
3. overlapping slices (self-aliasing views);
4. empty tensors (size-0 axes survive transpose/reshape/slice).

Plus failure scenarios that assert concrete error categories.
"""

from __future__ import annotations

from typing import Any

SCENARIOS: dict[str, list[dict[str, Any]]] = {}


def scenario(name: str):
    def register(fn):
        SCENARIOS[name] = fn()
        return fn
    return register


# --------------------------------------------------------------------- #
# 1. Transpose then reshape
# --------------------------------------------------------------------- #

@scenario("transpose_reshape_2d")
def _() -> list[dict[str, Any]]:
    data = [[1, 2, 3], [4, 5, 6]]
    return [
        {"op": "input", "name": "a", "data": data, "dtype": "int64"},
        # transpose is always a view (strides permuted)...
        {"op": "transpose", "name": "at", "input": "a"},
        # ...and reshaping the transposed (2,3)->(3,2) layout back to (6,)
        # forces a copy under C order.
        {"op": "reshape", "name": "flat", "input": "at",
         "shape": [6], "order": "C"},
        # The original C-contiguous array reshapes as a pure view.
        {"op": "reshape", "name": "flat_direct", "input": "a",
         "shape": [6], "order": "C"},
        # Reshape that only splits/merges contiguous axes stays a view even
        # on a transposed layout: (3,2) -> (3,2,1) inserts a unit axis.
        {"op": "reshape", "name": "unit_axis", "input": "at",
         "shape": [3, 2, 1], "order": "C"},
        # Fortran-order reshape of the transpose can stay a view.
        {"op": "reshape", "name": "flat_f", "input": "at",
         "shape": [6], "order": "F"},
    ]


@scenario("transpose_reshape_3d")
def _() -> list[dict[str, Any]]:
    # (2,3,4) fully reversed -> (4,3,2), strides (1,4,12); collapsing the
    # last two axes to (4,6) requires crossing the 12/4 stride boundary,
    # which even the lenient no-copy attempt cannot describe.
    data = [[[a * 12 + b * 4 + c + 1 for c in range(4)]
             for b in range(3)] for a in range(2)]
    return [
        {"op": "input", "name": "a", "data": data, "dtype": "int64"},
        {"op": "transpose", "name": "at", "input": "a", "axes": [2, 1, 0]},
        {"op": "reshape", "name": "r", "input": "at",
         "shape": [4, 6], "order": "C"},
        {"op": "reshape", "name": "r_never", "input": "at",
         "shape": [4, 6], "order": "C", "allow_copy": False,
         "expect_error": "NON_CONTIGUOUS_VIEW"},
        # Splitting only the innermost contiguous axis stays a view.
        {"op": "reshape", "name": "r_split", "input": "at",
         "shape": [4, 3, 2, 1], "order": "C"},
    ]


# --------------------------------------------------------------------- #
# 2. Broadcast zero strides
# --------------------------------------------------------------------- #

@scenario("broadcast_zero_stride")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "input", "name": "col",
         "data": [[10], [20], [30]], "dtype": "int64"},
        {"op": "broadcast_to", "name": "wide", "input": "col",
         "shape": [3, 4]},
        {"op": "input", "name": "row", "data": [[1, 2, 3, 4]], "dtype": "int64"},
        {"op": "broadcast_to", "name": "row_wide", "input": "row",
         "shape": [3, 4]},
        # Adding two zero-stride-broadcast operands: fresh result, values
        # match the NumPy outer-sum.
        {"op": "add", "name": "sum_grid", "input": "wide", "right": "row_wide"},
        # Slicing a broadcast axis keeps stride 0.
        {"op": "slice", "name": "wide_slice", "input": "wide",
         "index": [[0, 3, 1], [1, 4, 2]]},
    ]


# --------------------------------------------------------------------- #
# 3. Overlapping slices
# --------------------------------------------------------------------- #

@scenario("overlapping_slice")
def _() -> list[dict[str, Any]]:
    # as_strided synthesizes a (3, 3) window over 5 elements with stride 1:
    # rows overlap (this is the classic sliding-window view).
    return [
        {"op": "as_strided", "name": "win",
         "data": [1, 2, 3, 4, 5], "dtype": "int64",
         "shape": [3, 3], "strides": [1, 1], "offset": 0},
        {"op": "slice", "name": "win_tail", "input": "win",
         "index": [[1, 3, 1], [0, 3, 1]]},
        {"op": "materialize", "name": "win_copy", "input": "win",
         "order": "C"},
    ]


@scenario("overlapping_write_rejected")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "as_strided", "name": "win",
         "data": [0, 0, 0, 0, 0], "dtype": "int64",
         "shape": [3, 3], "strides": [1, 1]},
    ]


@scenario("overlapping_write_tempcopy")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "as_strided", "name": "win",
         "data": [1, 2, 3, 4, 5], "dtype": "int64",
         "shape": [3, 3], "strides": [1, 1]},
    ]


# --------------------------------------------------------------------- #
# 4. Empty tensors
# --------------------------------------------------------------------- #

@scenario("empty_tensors")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "input", "name": "empty23",
         "data": [[], [], []], "dtype": "float64"},
        {"op": "transpose", "name": "empty_t", "input": "empty23"},
        {"op": "reshape", "name": "empty_flat", "input": "empty23",
         "shape": [0], "order": "C"},
        {"op": "reshape", "name": "empty_3d", "input": "empty23",
         "shape": [2, 0, 5], "order": "C"},
        {"op": "slice", "name": "empty_slice", "input": "empty23",
         "index": [[0, 3, 2], [0, 0, 1]]},
        {"op": "broadcast_to", "name": "empty_bcast", "input": "empty23",
         "shape": [3, 0]},
        {"op": "neg", "name": "empty_neg", "input": "empty23"},
        {"op": "reduce_sum", "name": "empty_sum", "input": "empty23"},
        {"op": "reshape", "name": "empty_infer", "input": "empty_flat",
         "shape": [-1, 2], "order": "C"},
    ]


# --------------------------------------------------------------------- #
# Arithmetic, negative strides, reductions
# --------------------------------------------------------------------- #

@scenario("negative_strides_reverse")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "input", "name": "v",
         "data": [1, 2, 3, 4, 5, 6], "dtype": "int64"},
        {"op": "slice", "name": "rev", "input": "v",
         "index": [[None, None, -1]]},
        {"op": "slice", "name": "rev_step2", "input": "v",
         "index": [[None, None, -2]]},
        {"op": "reshape", "name": "rev_flat_copy", "input": "rev",
         "shape": [2, 3], "order": "C"},
        {"op": "scalar", "name": "rev_scaled", "input": "rev",
         "subop": "multiply", "value": 3},
    ]


@scenario("arithmetic_and_reduce")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "input", "name": "a",
         "data": [[1.0, 2.0], [3.0, 4.0]], "dtype": "float64"},
        {"op": "input", "name": "b",
         "data": [[5.0, 6.0], [7.0, 8.0]], "dtype": "float64"},
        {"op": "add", "name": "s", "input": "a", "right": "b"},
        {"op": "multiply", "name": "p", "input": "a", "right": "s"},
        {"op": "reduce_sum", "name": "total", "input": "p"},
        {"op": "reduce_sum", "name": "rowsum", "input": "p", "axis": 1},
        {"op": "reduce_sum", "name": "colsum_keep", "input": "p",
         "axis": 0, "keepdims": True},
        {"op": "equal", "name": "mask", "input": "a", "right": "a"},
    ]


# --------------------------------------------------------------------- #
# Failure categories
# --------------------------------------------------------------------- #

@scenario("failure_categories")
def _() -> list[dict[str, Any]]:
    return [
        {"op": "input", "name": "a",
         "data": [[1, 2], [3, 4]], "dtype": "int64"},
        {"op": "reshape", "name": "bad_size", "input": "a",
         "shape": [5], "order": "C",
         "expect_error": "SIZE_MISMATCH"},
        {"op": "reshape", "name": "two_unknown", "input": "a",
         "shape": [-1, -1], "order": "C",
         "expect_error": "SIZE_MISMATCH"},
        {"op": "slice", "name": "oob", "input": "a",
         "index": [5], "expect_error": "INDEX_OUT_OF_BOUNDS"},
        {"op": "slice", "name": "neg_oob", "input": "a",
         "index": [-9], "expect_error": "INDEX_OUT_OF_BOUNDS"},
        {"op": "slice", "name": "zero_step", "input": "a",
         "index": [[0, 2, 0]], "expect_error": "INVALID_INDEX"},
        {"op": "transpose", "name": "bad_axes", "input": "a",
         "axes": [0, 0], "expect_error": "SHAPE_MISMATCH"},
    ]
