"""Library-level examples: slice / transpose / reshape / ops / verification.

Run:  python3 scripts/examples.py
No server required -- this talks to the core packages directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from tensorcraft import Tensor
from tensorcraft.tensor import Layout, Storage, ops, resolve_dtype
from tensorcraft.validation import fixtures, run_all_checks, run_scenario


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    section("1. transpose is a view; transpose+reshape copies")
    base = Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")
    transposed = base.transpose()
    flat = transposed.reshape((6,), "C")
    print("transposed aliases base:", transposed.shares_storage_with(base))
    print("flat aliases transposed:", flat.shares_storage_with(transposed))
    print("copied values:", flat.to_numpy().tolist())
    print("direct reshape aliases base:",
          base.reshape((6,), "C").shares_storage_with(base))

    section("2. negative strides (reverse)")
    vector = Tensor.from_nested(list(range(8)), "int64")
    reversed_view = vector.getitem((slice(None, None, -2),))
    print("values:", reversed_view.to_numpy().tolist(),
          "strides:", reversed_view.strides)

    section("3. broadcasting uses zero strides, no expansion")
    column = Tensor.from_nested([[10], [20], [30]], "int64")
    wide = column.broadcast_to((3, 4))
    print("strides:", wide.strides, "aliases column:",
          wide.shares_storage_with(column))

    section("4. overlapping write: reject vs deterministic temp copy")
    storage = Storage(np.arange(1, 6, dtype=np.int64),
                      resolve_dtype("int64"))
    window = Tensor(storage, Layout((3, 3), (1, 1), 0))
    print("self-overlapping:", window.is_self_overlapping())
    try:
        window.assign_scalar(7, policy="reject")
    except Exception as exc:
        print("rejected:", exc.category, "-", exc.details["unique_elements"],
              "unique elements over", exc.details["positions"], "positions")

    section("5. arithmetic on non-contiguous operands")
    grid = Tensor.from_nested(np.arange(12).reshape(3, 4).tolist(), "int64")
    doubled = ops.scalar_op(grid.transpose(), 2, "multiply").tensor
    print(doubled.to_numpy().tolist())

    section("6. differential verification against the NumPy oracle")
    report = run_scenario(fixtures.SCENARIOS["transpose_reshape_3d"])
    print("3d scenario:", report.summary())
    empty_report = run_scenario(fixtures.SCENARIOS["empty_tensors"])
    print("empty tensors:", empty_report.summary(),
          "(uncertainties listed separately:",
          len(empty_report.uncertainties), ")")

    section("7. memory contract checks")
    for check in run_all_checks():
        print(f"[{'PASS' if check.passed else 'FAIL'}] {check.name}")


if __name__ == "__main__":
    main()
