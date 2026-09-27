"""Generate tests/fixtures/reference_grads.json.

Reference losses and gradients are computed with *pure NumPy* using
hand-derived analytic formulas — the minigrad autodiff engine is not
imported anywhere in this script. The test suite then checks the engine
against these independent references.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "reference_grads.json"


def case_broadcast_sum():
    # L = sum(x * b) + sum(b);  dL/dx_ij = b_j,  dL/db_j = sum_i x_ij + 1
    x = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    b = np.array([0.5, -1.0, 2.0])
    loss = float((x * b).sum() + b.sum())
    return {
        "params": {"x": x.tolist(), "b": b.tolist()},
        "loss": loss,
        "grads": {
            "x": np.broadcast_to(b, x.shape).tolist(),
            "b": (x.sum(axis=0) + 1.0).tolist(),
        },
    }


def case_shared_branch():
    # L = sum(x * x) + sum(x);  dL/dx = 2x + 1  (x feeds two consumers)
    x = np.array([1.0, -2.0, 0.5])
    loss = float((x * x).sum() + x.sum())
    return {
        "params": {"x": x.tolist()},
        "loss": loss,
        "grads": {"x": (2.0 * x + 1.0).tolist()},
    }


def case_matmul_2d():
    # L = sum(A @ B);  dL/dA = ones @ B.T,  dL/dB = A.T @ ones
    a = np.array([[1.0, 2.0, 3.0], [0.5, -1.5, 2.5]])
    b = np.array([[0.5, -1.0], [2.0, 0.5], [-1.5, 1.0]])
    ones = np.ones((2, 2))
    loss = float((a @ b).sum())
    return {
        "params": {"a": a.tolist(), "b": b.tolist()},
        "loss": loss,
        "grads": {
            "a": (ones @ b.T).tolist(),
            "b": (a.T @ ones).tolist(),
        },
    }


def case_mean_reduction():
    # L = mean(x ** 2);  dL/dx = 2x / x.size
    x = np.array([[1.0, -2.0, 3.0], [-4.0, 0.5, 2.0]])
    loss = float((x ** 2).mean())
    return {
        "params": {"x": x.tolist()},
        "loss": loss,
        "grads": {"x": (2.0 * x / x.size).tolist()},
    }


def main():
    cases = {
        "broadcast_sum": case_broadcast_sum(),
        "shared_branch": case_shared_branch(),
        "matmul_2d": case_matmul_2d(),
        "mean_reduction": case_mean_reduction(),
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {FIXTURE_PATH} with {len(cases)} cases")


if __name__ == "__main__":
    main()
