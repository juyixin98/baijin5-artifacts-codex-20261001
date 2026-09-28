"""Explicit alias, overlap and write-semantics checks.

Unlike :mod:`tensorcraft.validation.verifier` (differential scenarios),
this module asserts the *memory contract* directly:

* a view shares storage and mutates the parent when written (alias);
* a materialized result does not;
* writes into self-overlapping views are rejected under ``reject`` and
  produce a defined result under ``temp_copy``;
* broadcasting produces zero strides and addresses each source element
  the expected number of times.

Reference behaviour for values is computed with plain NumPy, and the
"what NumPy itself does" note records NumPy's own (unsafe) overlap write
for contrast, so the policy choice stays explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..errors import OverlapWriteError
from ..tensor import Layout, Storage, Tensor, resolve_dtype
from . import oracle


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    facts: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": bool(self.passed),
                "detail": self.detail, "facts": _jsonable(self.facts)}


def _jsonable(value: Any) -> Any:
    """Recursively coerce numpy scalars/arrays to JSON-native types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _overlapping_window() -> Tensor:
    """Sliding-window (3,3) view over 5 elements, strides (1,1)."""
    storage = Storage(
        np.array([1, 2, 3, 4, 5], dtype=np.int64), resolve_dtype("int64"))
    return Tensor(storage, Layout((3, 3), (1, 1), 0))


def check_view_aliases_parent() -> CheckResult:
    base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
    view = base.getitem((slice(None), slice(0, 2)))
    aliases = bool(view.shares_storage_with(base))
    report = view.assign_scalar(9, policy="reject")
    parent_changed = bool(base.to_numpy()[0, 0] == 9)
    passed = aliases and parent_changed and not report.temp_copy
    return CheckResult(
        "view_aliases_parent", passed,
        "slice view shares storage; a legal write is visible through parent",
        {"aliases": aliases, "parent_changed": parent_changed,
         "temp_copy": report.temp_copy})


def check_materialize_independent() -> CheckResult:
    base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
    copy = base.getitem((slice(None), slice(0, 2))).materialize()
    aliases = bool(copy.shares_storage_with(base))
    copy.assign_scalar(0, policy="reject")
    parent_untouched = bool(base.to_numpy()[0, 0] == 1)
    passed = (not aliases) and parent_untouched
    return CheckResult(
        "materialize_independent", passed,
        "materialize allocates fresh storage; writes stay local",
        {"aliases": aliases, "parent_untouched": parent_untouched})


def check_overlap_write_rejected() -> CheckResult:
    window = _overlapping_window()
    overlapping = bool(window.is_self_overlapping())
    raised_category = None
    try:
        window.assign_scalar(7, policy="reject")
    except OverlapWriteError as exc:
        raised_category = exc.category
        facts = exc.details
    else:
        facts = {}
    # Under reject, the buffer must remain exactly as it was.
    untouched = bool(np.array_equal(
        window.storage.buffer, np.array([1, 2, 3, 4, 5], dtype=np.int64)))
    passed = overlapping and raised_category == "OVERLAPPING_WRITE" and untouched
    return CheckResult(
        "overlap_write_rejected", passed,
        "overlapping fill is refused with OVERLAPPING_WRITE and nothing is written",
        {"overlapping": overlapping,
         "raised_category": raised_category,
         "buffer_untouched": untouched, **facts})


def check_overlap_write_temp_copy() -> CheckResult:
    window = _overlapping_window()
    # Deterministic policy: last C-order write wins, so every storage
    # element receives the same scalar here -> uniform fill.
    report = window.assign_scalar(7, policy="temp_copy")
    result = window.to_numpy()
    expected = np.full((3, 3), 7, dtype=np.int64)
    values_ok = bool(np.array_equal(result, expected))
    buffer = window.storage.buffer
    # What NumPy itself does with the same overlapping fill (for contrast).
    ref_base = np.arange(1, 6, dtype=np.int64)
    ref_view = oracle.ref_as_strided(ref_base, "int64", (3, 3), (1, 1))
    ref_view[:] = 7
    numpy_buffer = ref_base.copy()
    passed = values_ok and report.temp_copy
    return CheckResult(
        "overlap_write_temp_copy", passed,
        "temp_copy gives a deterministic uniform fill on a full overlap",
        {"temp_copy": report.temp_copy,
         "values_match": values_ok,
         "result": result.tolist(),
         "final_buffer": buffer.tolist(),
         "numpy_overlapping_fill_buffer_for_contrast": numpy_buffer.tolist()})


def check_overlapping_tensor_assign_deterministic() -> CheckResult:
    """Non-uniform source into an overlapping destination is order-safe."""
    base_values = np.arange(1, 6, dtype=np.int64)
    storage = Storage(base_values.copy(), resolve_dtype("int64"))
    window = Tensor(storage, Layout((3, 3), (1, 1), 0))
    # Source is a separate non-overlapping (3,3) tensor.
    source = Tensor.from_nested(
        [[10, 11, 12], [20, 21, 22], [30, 31, 32]], "int64")
    report = window.assign(source, policy="temp_copy")
    result = window.to_numpy()
    # Scatter in C order; per-offset last writer:
    #   offset 0 <- src[0,0]=10
    #   offset 1 <- src[1,0]=20 (later than src[0,1]=11)
    #   offset 2 <- src[2,0]=30 (later than 12 and 21)
    #   offset 3 <- src[2,1]=31, offset 4 <- src[2,2]=32
    expected = np.array(
        [[10, 20, 30], [20, 30, 31], [30, 31, 32]], dtype=np.int64)
    values_ok = bool(np.array_equal(result, expected))
    # Run it several times: the answer must not depend on execution order.
    stable = all(
        np.array_equal(_rerun_temp_copy(base_values, source), expected)
        for _ in range(5))
    return CheckResult(
        "overlapping_assign_deterministic", values_ok and stable,
        "temp_copy scatter is deterministic (last C-order write wins)",
        {"values_match": values_ok, "stable_across_runs": stable,
         "result": result.tolist(), "expected": expected.tolist()})


def _rerun_temp_copy(base_values: np.ndarray, source: Tensor) -> np.ndarray:
    storage = Storage(base_values.copy(), resolve_dtype("int64"))
    window = Tensor(storage, Layout((3, 3), (1, 1), 0))
    window.assign(source, policy="temp_copy")
    return window.to_numpy()


def check_broadcast_zero_strides() -> CheckResult:
    column = Tensor.from_nested([[10], [20], [30]], "int64")
    wide = column.broadcast_to((3, 4))
    strides_ok = wide.strides == (1, 0)
    addressed = wide.addressed_offsets()
    # Each of the 3 source elements is addressed 4 times.
    counts = {offset: addressed.count(offset) for offset in set(addressed)}
    counts_ok = counts == {0: 4, 1: 4, 2: 4}
    values_ok = bool(np.array_equal(
        wide.to_numpy(), np.broadcast_to(np.array([[10], [20], [30]]), (3, 4))))
    aliases = bool(wide.shares_storage_with(column))
    return CheckResult(
        "broadcast_zero_strides",
        strides_ok and counts_ok and values_ok and aliases,
        "broadcast axis has stride 0 and reuses each source element",
        {"strides": list(wide.strides), "offset_counts": counts,
         "values_match": values_ok, "aliases_source": aliases})


def check_transpose_then_reshape_decision() -> CheckResult:
    base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
    transposed = base.transpose()
    t_is_view = bool(transposed.shares_storage_with(base))
    reshaped = transposed.reshape((6,), "C")
    r_copied = bool(not reshaped.shares_storage_with(transposed))
    # NumPy agrees on the copy; compare values too.
    ref = np.arange(1, 7, dtype=np.int64).reshape(2, 3)
    ref_values = ref.T.reshape(6)
    values_ok = bool(np.array_equal(reshaped.to_numpy(), ref_values))
    # The same reshape on the original contiguous tensor is a view.
    direct = base.reshape((6,), "C")
    direct_view = bool(direct.shares_storage_with(base))
    passed = t_is_view and r_copied and values_ok and direct_view
    return CheckResult(
        "transpose_then_reshape_decision", passed,
        "transpose=view, transpose+reshape=copy, contiguous reshape=view",
        {"transpose_is_view": t_is_view,
         "transposed_reshape_copied": r_copied,
         "contiguous_reshape_is_view": direct_view,
         "values_match": values_ok,
         "copied_values": reshaped.to_numpy().tolist()})


def run_all_checks() -> list[CheckResult]:
    return [
        check_view_aliases_parent(),
        check_materialize_independent(),
        check_overlap_write_rejected(),
        check_overlap_write_temp_copy(),
        check_overlapping_tensor_assign_deterministic(),
        check_broadcast_zero_strides(),
        check_transpose_then_reshape_decision(),
    ]
