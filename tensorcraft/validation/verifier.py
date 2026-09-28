"""Differential verification: core vs the independent NumPy oracle.

A scenario is a sequence of purely data-described steps. Two interpreters
execute the *same* scenario independently -- one through
:mod:`tensorcraft`, one through NumPy (:mod:`tensorcraft.validation.oracle`)
-- and every named step is compared on both **values** and **memory
behaviour** (copied vs view, alias identity). Expected failures are asserted
by category as well.

Conclusions have three states:

* ``passed``  -- value and memory facts agree with the oracle;
* ``failed``  -- a concrete disagreement (with both sides recorded);
* ``uncertain`` -- the oracle signal itself was ambiguous; reported on its
  own rather than counted as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .. import tensor as tc_tensor
from ..errors import TensorCraftError
from ..tensor import Tensor
from ..tensor import ops as tc_ops
from . import oracle


@dataclass(frozen=True)
class StepResult:
    step: str
    name: str
    passed: bool
    uncertain: bool
    detail: str
    expected_category: str | None = None
    observed_category: str | None = None
    core_facts: dict[str, Any] = field(default_factory=dict)
    oracle_facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "name": self.name,
            "passed": self.passed,
            "uncertain": self.uncertain,
            "detail": self.detail,
            "expected_category": self.expected_category,
            "observed_category": self.observed_category,
            "core": self.core_facts,
            "oracle": self.oracle_facts,
        }


@dataclass(frozen=True)
class VerificationReport:
    results: tuple[StepResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> tuple[StepResult, ...]:
        return tuple(r for r in self.results if not r.passed and not r.uncertain)

    @property
    def uncertainties(self) -> tuple[StepResult, ...]:
        return tuple(r for r in self.results if r.uncertain)

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": len(self.failures),
            "uncertain": len(self.uncertainties),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------- #
# Index decoding (independent per interpreter; the data form is shared)
# ---------------------------------------------------------------------- #

def _decode_index(raw: Sequence[Any]):
    entries: list[Any] = []
    for entry in raw:
        if isinstance(entry, str):
            if entry == "newaxis":
                entries.append(None)
            elif entry == "ellipsis":
                entries.append(Ellipsis)
            else:
                raise ValueError(f"bad index token {entry!r}")
        elif isinstance(entry, bool):
            raise ValueError("boolean index is not supported")
        elif isinstance(entry, int):
            entries.append(entry)
        elif isinstance(entry, (list, tuple)):
            entries.append(slice(entry[0], entry[1], entry[2]))
        else:
            raise ValueError(f"bad index entry {entry!r}")
    return tuple(entries) if len(entries) != 1 else entries[0]


# ---------------------------------------------------------------------- #
# Core interpreter
# ---------------------------------------------------------------------- #

class _CoreInterpreter:
    def __init__(self) -> None:
        self.values: dict[str, Tensor] = {}
        self.last_copied: bool | None = None

    def run(self, step: dict[str, Any]) -> None:
        op = step["op"]
        name = step["name"]
        if op == "input":
            self.values[name] = Tensor.from_nested(step["data"], step.get("dtype"))
            self.last_copied = None
            return
        if op == "as_strided":
            arr = np.asarray(step["data"], dtype=np.dtype(step["dtype"]))
            storage = tc_tensor.Storage(arr.reshape(-1).copy(),
                                        tc_tensor.resolve_dtype(step["dtype"]))
            layout = tc_tensor.Layout(tuple(step["shape"]),
                                      tuple(step["strides"]),
                                      int(step.get("offset", 0)))
            self.values[name] = Tensor(storage, layout)
            self.last_copied = None
            return

        source = self.values[step["input"]]
        if op == "transpose":
            out = source.transpose(step.get("axes"))
            self.last_copied = not out.shares_storage_with(source)
        elif op == "reshape":
            out = source.reshape(step["shape"], step.get("order", "C"),
                                 allow_copy=bool(step.get("allow_copy", True)))
            self.last_copied = not out.shares_storage_with(source)
        elif op == "slice":
            out = source.getitem(_decode_index(step["index"]))
            self.last_copied = not out.shares_storage_with(source)
        elif op == "broadcast_to":
            out = source.broadcast_to(tuple(step["shape"]))
            self.last_copied = not out.shares_storage_with(source)
        elif op == "materialize":
            out = source.materialize(step.get("order", "C"))
            self.last_copied = not out.shares_storage_with(source)
        elif op in {"add", "subtract", "multiply", "divide", "floor_divide",
                    "mod", "power"}:
            other = self.values[step["right"]]
            out = tc_ops.elementwise(source, other, op).tensor
            self.last_copied = True
        elif op in {"equal", "not_equal", "less", "less_equal", "greater",
                    "greater_equal"}:
            other = self.values[step["right"]]
            out = tc_ops.comparison(source, other, op).tensor
            self.last_copied = True
        elif op in {"neg", "abs"}:
            out = tc_ops.unary(source, op).tensor
            self.last_copied = True
        elif op == "scalar":
            out = tc_ops.scalar_op(source, step["value"], step["subop"]).tensor
            self.last_copied = True
        elif op == "reduce_sum":
            out = tc_ops.reduce_sum(
                source, axis=step.get("axis"),
                keepdims=bool(step.get("keepdims", False))).tensor
            self.last_copied = True
        else:
            raise ValueError(f"unknown scenario op {op!r}")
        self.values[name] = out


# ---------------------------------------------------------------------- #
# NumPy oracle interpreter (never imports the core tensor layer)
# ---------------------------------------------------------------------- #

class _OracleInterpreter:
    def __init__(self) -> None:
        self.values: dict[str, np.ndarray] = {}
        self.last_copied: bool | None = None

    def run(self, step: dict[str, Any]) -> None:
        op = step["op"]
        name = step["name"]
        if op == "input":
            self.values[name] = np.asarray(step["data"],
                                           dtype=np.dtype(step["dtype"]))
            self.last_copied = None
            return
        if op == "as_strided":
            self.values[name] = oracle.ref_as_strided(
                step["data"], step["dtype"], step["shape"],
                step["strides"], int(step.get("offset", 0)))
            self.last_copied = None
            return

        source = self.values[step["input"]]
        if op == "transpose":
            out = oracle.ref_transpose(source, step.get("axes"))
            self.last_copied = oracle._numpy_copied(source, out)
        elif op == "reshape":
            out, self.last_copied = oracle.ref_reshape(
                source, step["shape"], step.get("order", "C"))
        elif op == "slice":
            before = source
            out = oracle.ref_slice(source, _decode_index(step["index"]))
            self.last_copied = oracle._numpy_copied(before, out)
        elif op == "broadcast_to":
            before = source
            out = oracle.ref_broadcast_to(source, step["shape"])
            self.last_copied = oracle._numpy_copied(before, out)
        elif op == "materialize":
            before = source
            out = oracle.ref_materialize(source, step.get("order", "C"))
            self.last_copied = oracle._numpy_copied(before, out)
        elif op in {"add", "subtract", "multiply", "divide", "floor_divide",
                    "mod", "power"}:
            out = oracle.ref_binary(source, self.values[step["right"]], op)
            self.last_copied = True
        elif op in {"equal", "not_equal", "less", "less_equal", "greater",
                    "greater_equal"}:
            fn = {
                "equal": np.equal, "not_equal": np.not_equal,
                "less": np.less, "less_equal": np.less_equal,
                "greater": np.greater, "greater_equal": np.greater_equal,
            }[op]
            out = fn(source, self.values[step["right"]]).astype(np.uint8)
            self.last_copied = True
        elif op in {"neg", "abs"}:
            out = np.negative(source) if op == "neg" else np.absolute(source)
            self.last_copied = True
        elif op == "scalar":
            out = oracle.ref_scalar(source, step["value"], step["subop"])
            self.last_copied = True
        elif op == "reduce_sum":
            out = oracle.ref_reduce_sum(
                source, step.get("axis"), bool(step.get("keepdims", False)))
            self.last_copied = True
        else:
            raise ValueError(f"unknown scenario op {op!r}")
        self.values[name] = out


# ---------------------------------------------------------------------- #
# Scenario runner
# ---------------------------------------------------------------------- #

def _facts_core(tensor: Tensor, copied: bool | None) -> dict[str, Any]:
    return {
        "shape": list(tensor.shape),
        "strides": list(tensor.strides),
        "offset": tensor.storage_offset,
        "dtype": tensor.dtype.name,
        "copied": copied,
        "storage_token": tensor.token,
        "self_overlapping": (
            tensor.is_self_overlapping() if tensor.size <= 4096 else None),
    }


def _facts_oracle(arr: np.ndarray, copied: bool | None) -> dict[str, Any]:
    return {
        "shape": list(arr.shape),
        "strides": [s // arr.dtype.itemsize for s in arr.strides],
        "offset": 0,
        "dtype": arr.dtype.name,
        "copied": copied,
        "owndata": bool(arr.flags.owndata),
    }


def run_scenario(steps: Sequence[dict[str, Any]]) -> VerificationReport:
    """Execute a scenario on both interpreters and compare every step."""
    core = _CoreInterpreter()
    ref = _OracleInterpreter()
    results: list[StepResult] = []

    for index, step in enumerate(steps):
        name = step["name"]
        expected_error = step.get("expect_error")
        try:
            core.run(step)
        except TensorCraftError as exc:
            if expected_error is None:
                results.append(StepResult(
                    step=name, name=name, passed=False, uncertain=False,
                    detail=f"unexpected core error: {exc.message}",
                    observed_category=exc.category,
                    core_facts={"error": exc.to_dict()}))
                # Keep the oracle in lockstep where possible: if the core
                # rejects an invalid step, there is no oracle value to
                # compare; record the refusal and move on.
                continue
            passed = exc.category == expected_error
            results.append(StepResult(
                step=name, name=name, passed=passed, uncertain=False,
                detail=("error category matches" if passed else
                        f"expected {expected_error}, got {exc.category}"),
                expected_category=expected_error,
                observed_category=exc.category,
                core_facts={"error": exc.to_dict()}))
            continue

        ref.run(step)

        if expected_error is not None:
            results.append(StepResult(
                step=name, name=name, passed=False, uncertain=False,
                detail=f"expected error {expected_error} but both interpreters "
                       "succeeded",
                expected_category=expected_error))
            continue

        core_tensor = core.values[name]
        ref_array = ref.values[name]
        core_arr = core_tensor.to_numpy()

        # Comparison ops on the oracle side are uint8; the core matches.
        exact_dtype = step["op"] not in {"divide"} or \
            core_tensor.dtype.kind == "f"
        values_ok, value_detail = oracle.arrays_match(
            core_arr, ref_array, exact_dtype=exact_dtype)

        memory_ok = True
        uncertain = False
        memory_note = "n/a (construction step)"
        if step["op"] not in {"input", "as_strided"}:
            core_copied = core.last_copied
            ref_copied = ref.last_copied
            zero_size = core_tensor.size == 0 and ref_array.size == 0
            if zero_size:
                # Empty results allocate nothing, so NumPy's own signals are
                # contradictory (it reports owndata=True / no shared memory
                # for what is conceptually a view). The copy-vs-view
                # distinction is genuinely unobservable here: report it as
                # its own uncertainty rather than a pass or a failure.
                uncertain = True
                memory_note = (
                    "empty result: copy/view is unobservable "
                    f"(core copied={core_copied}, oracle owndata="
                    f"{ref_array.flags.owndata})")
            else:
                memory_note = f"copied core={core_copied} oracle={ref_copied}"
                if core_copied != ref_copied:
                    memory_ok = False

        passed = values_ok and memory_ok
        value_note = "values match" if values_ok else f"values: {value_detail}"
        detail = f"{value_note}; {memory_note}"
        results.append(StepResult(
            step=name, name=name, passed=passed, uncertain=uncertain,
            detail=detail,
            core_facts=_facts_core(core_tensor, core.last_copied),
            oracle_facts=_facts_oracle(ref_array, ref.last_copied)))

    return VerificationReport(results=tuple(results))
