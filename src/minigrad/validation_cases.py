"""Validation cases for finite-difference gradcheck.

Each case pairs:
* ``numpy_fn``  — the scalar loss written in pure NumPy. This is the
  *independent reference implementation*: finite differences perturb its
  inputs, so the expected gradients never come from the autodiff engine.
* ``tensor_fn`` — the same loss written with minigrad tensors (the system
  under test).

Coverage: broadcasting, multi-branch shared nodes, matmul (matrix-matrix and
matrix-vector), reductions (sum / mean / max), activation chains, and empty
dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .errors import UnknownCaseError


@dataclass(frozen=True)
class Case:
    name: str
    params: dict
    numpy_fn: Callable[[dict], float]
    tensor_fn: Callable[[dict], object]


def _build_cases() -> dict:
    x = np.array([[0.2, -0.5, 1.3], [0.7, 0.9, -1.1]])
    b = np.array([0.3, -0.2, 0.5])
    c = np.array(0.4)
    v = np.array([0.6, -0.8, 0.1])
    w = np.array([1.1, -0.4, 0.9])
    a_mat = np.array([[0.5, -1.2, 0.3], [2.0, 0.1, -0.7]])
    b_mat = np.array([[0.4, -0.6], [1.5, 0.2], [-0.9, 1.1]])
    c_mat = np.array([[0.7, -0.3], [0.2, 1.4]])
    x_empty = np.zeros((0, 3))

    cases = [
        Case(
            name="broadcast_add_mul",
            params={"x": x, "b": b, "c": c},
            numpy_fn=lambda p: float(((p["x"] * p["b"] + p["c"]) ** 2).sum()),
            tensor_fn=lambda t: ((t["x"] * t["b"] + t["c"]) ** 2).sum(),
        ),
        Case(
            name="shared_multi_branch",
            params={"x": v, "y": w},
            numpy_fn=lambda p: float(
                ((p["x"] * p["y"]) * (p["x"] + p["y"])).sum() + p["x"].sum()
            ),
            tensor_fn=lambda t: (
                (t["x"] * t["y"]) * (t["x"] + t["y"])
            ).sum() + t["x"].sum(),
        ),
        Case(
            name="matmul_2d",
            params={"a": a_mat, "b": b_mat, "c": c_mat},
            numpy_fn=lambda p: float(((p["a"] @ p["b"]) * p["c"]).sum()),
            tensor_fn=lambda t: ((t["a"] @ t["b"]) * t["c"]).sum(),
        ),
        Case(
            name="matmul_vector",
            params={"a": a_mat, "v": v},
            numpy_fn=lambda p: float(
                (1.0 / (1.0 + np.exp(-(p["a"] @ p["v"])))).sum()
            ),
            tensor_fn=lambda t: _sigmoid(t["a"] @ t["v"]).sum(),
        ),
        Case(
            name="reduction_mean",
            params={"x": x},
            numpy_fn=lambda p: float((p["x"] ** 2).mean(axis=1).sum()),
            tensor_fn=lambda t: (t["x"] ** 2).mean(axis=1).sum(),
        ),
        Case(
            name="reduction_max",
            params={"x": x},
            numpy_fn=lambda p: float(p["x"].max(axis=1).sum()),
            tensor_fn=lambda t: t["x"].max(axis=1).sum(),
        ),
        Case(
            name="activation_chain",
            params={"x": v, "w": w},
            numpy_fn=lambda p: float(
                np.tanh(p["w"] / (1.0 + np.exp(-p["x"]))).sum()
            ),
            tensor_fn=lambda t: _tanh(_sigmoid(t["x"]) * t["w"]).sum(),
        ),
        Case(
            name="empty_dim",
            params={"x": x_empty, "b": b},
            numpy_fn=lambda p: float(
                (p["x"] * p["b"]).sum() + (p["b"] ** 2).sum()
            ),
            tensor_fn=lambda t: (t["x"] * t["b"]).sum() + (t["b"] ** 2).sum(),
        ),
    ]
    return {case.name: case for case in cases}


def _sigmoid(t):
    from . import ops
    return ops.sigmoid(t)


def _tanh(t):
    from . import ops
    return ops.tanh(t)


_CASES = _build_cases()


def list_cases() -> list:
    return sorted(_CASES)


def get_case(name: str) -> Case:
    try:
        return _CASES[name]
    except KeyError:
        raise UnknownCaseError(name) from None
