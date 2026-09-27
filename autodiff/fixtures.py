"""Reusable synthetic fixtures shared by tests, the CLI and the HTTP API.

Each scenario bundles:

* ``build_graph``  - constructs core tensors and runs the forward pass under
  test, returning the scalar loss and the named leaf tensors;
* ``reference``    - an *independent plain-NumPy* scalar function of the same
  named raw arrays.  It never imports :mod:`autodiff`, so expected gradients
  come from outside the system under test.

All randomness is seeded per scenario for deterministic, replayable results.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

import numpy as np

from . import ops
from .tensor import Tensor

ReferenceFn = Callable[[Dict[str, np.ndarray]], float]


@dataclass(frozen=True)
class BuiltGraph:
    loss: Tensor
    leaves: Dict[str, Tensor]
    base_inputs: Dict[str, np.ndarray]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    build_graph: Callable[[], BuiltGraph]
    reference: ReferenceFn

    def run(self) -> tuple[BuiltGraph, ReferenceFn]:
        return self.build_graph(), self.reference


# ---------------------------------------------------------------------------
# Independent reference functions (pure numpy, no autodiff imports)
# ---------------------------------------------------------------------------


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def ref_broadcast_add(d: Dict[str, np.ndarray]) -> float:
    x, b, c = d["x"], d["bias"], d["col"]
    return float(np.sum((x + b + c) ** 2))


def ref_matmul_mlp(d: Dict[str, np.ndarray]) -> float:
    return float(np.sum(_sigmoid(d["x"] @ d["w"] + d["b"])))


def ref_shared_branch(d: Dict[str, np.ndarray]) -> float:
    h = d["x"] @ d["w"]
    hs = np.sum(h)
    # h feeds two downstream branches and is also scaled by a constant factor.
    return float(np.sum(h * h) + hs * hs + 0.5 * hs)


def ref_activations(d: Dict[str, np.ndarray]) -> float:
    x = d["x"]
    return float(np.sum(np.maximum(x, 0.0) * _sigmoid(x) + np.tanh(x)))


def ref_reduction(d: Dict[str, np.ndarray]) -> float:
    x = d["x"]
    # mean over axis 0, then sum; plus global mean term.
    return float(np.sum(np.mean(x, axis=0)) + 3.0 * np.mean(x))


def ref_empty_matmul(d: Dict[str, np.ndarray]) -> float:
    # (2,0) @ (0,2) is a (2,2) zero matrix; the loss is identically zero and
    # every partial derivative is zero.  The reference still uses plain numpy
    # semantics so it stays independent of the core's empty-axis handling.
    return float(np.sum(d["p"] @ d["q"]))


def ref_empty_mean(d: Dict[str, np.ndarray]) -> float:
    # A (2,0) array has no elements per row: each empty mean is the defined
    # zero used by the core policy, so the scalar loss is identically 0.
    # Summing an empty numpy array is a warning-free, independent definition.
    return float(np.sum(d["x"]))


def ref_div_exp_log(d: Dict[str, np.ndarray]) -> float:
    x, y = d["x"], d["y"]
    # Keep x strictly positive so log is defined over the FD neighbourhood.
    return float(np.sum(np.exp(-x) + np.log(y) + x / y))


def ref_batched_matmul(d: Dict[str, np.ndarray]) -> float:
    # Broadcasting batch matmul: (2,1,3) x (1,3,4) -> (2,2,4)
    a = d["a"]
    b = d["b"]
    return float(np.sum(a @ b))


# ---------------------------------------------------------------------------
# Scenario builders (core forward passes)
# ---------------------------------------------------------------------------


# Fixed per-scenario seeds (never hash(): Python salts str hashes per process,
# which would make fixtures non-replayable across runs).
_SEEDS: Dict[str, int] = {
    "broadcast_add": 1101,
    "matmul_mlp": 1102,
    "shared_branch": 1103,
    "activations": 1104,
    "reduction": 1105,
    "div_exp_log": 1106,
    "batched_matmul": 1107,
}


def _seeded(name: str) -> np.random.Generator:
    return np.random.default_rng(_SEEDS[name])


def build_broadcast_add() -> BuiltGraph:
    rng = _seeded("broadcast_add")
    x = Tensor(rng.normal(size=(2, 3)) * 0.5, requires_grad=True)
    bias = Tensor(rng.normal(size=(3,)) * 0.3, requires_grad=True)
    col = Tensor(rng.normal(size=(2, 1)) * 0.3, requires_grad=True)
    z = x + bias + col
    loss = (z * z).sum()
    return BuiltGraph(loss, {"x": x, "bias": bias, "col": col},
                      {"x": x.data.copy(), "bias": bias.data.copy(),
                       "col": col.data.copy()})


def build_matmul_mlp() -> BuiltGraph:
    rng = _seeded("matmul_mlp")
    x = Tensor(rng.normal(size=(2, 3)), requires_grad=True)
    w = Tensor(rng.normal(size=(3, 2)) * 0.4, requires_grad=True)
    b = Tensor(rng.normal(size=(2,)) * 0.1, requires_grad=True)
    loss = ops.sigmoid(ops.matmul(x, w) + b).sum()
    return BuiltGraph(loss, {"x": x, "w": w, "b": b},
                      {"x": x.data.copy(), "w": w.data.copy(), "b": b.data.copy()})


def build_shared_branch() -> BuiltGraph:
    rng = _seeded("shared_branch")
    x = Tensor(rng.normal(size=(3,)), requires_grad=True)
    w = Tensor(rng.normal(size=(3, 2)) * 0.5, requires_grad=True)
    h = ops.matmul(x, w)  # shared intermediate, two consumers
    hs = h.sum()
    loss = (h * h).sum() + hs * hs + hs * 0.5
    return BuiltGraph(loss, {"x": x, "w": w},
                      {"x": x.data.copy(), "w": w.data.copy()})


def build_activations() -> BuiltGraph:
    rng = _seeded("activations")
    x = Tensor(rng.normal(size=(4,)), requires_grad=True)
    loss = (ops.relu(x) * ops.sigmoid(x) + ops.tanh(x)).sum()
    return BuiltGraph(loss, {"x": x}, {"x": x.data.copy()})


def build_reduction() -> BuiltGraph:
    rng = _seeded("reduction")
    x = Tensor(rng.normal(size=(2, 3)), requires_grad=True)
    loss = x.mean(axis=0).sum() + x.mean() * 3.0
    return BuiltGraph(loss, {"x": x}, {"x": x.data.copy()})


def build_empty_matmul() -> BuiltGraph:
    p = Tensor(np.zeros((2, 0)), requires_grad=True)
    q = Tensor(np.zeros((0, 2)), requires_grad=True)
    loss = ops.matmul(p, q).sum()
    return BuiltGraph(loss, {"p": p, "q": q},
                      {"p": p.data.copy(), "q": q.data.copy()})


def build_empty_mean() -> BuiltGraph:
    # (2,0): mean over the empty axis 1 is a per-row defined zero.
    x = Tensor(np.zeros((2, 0)), requires_grad=True)
    loss = x.mean(axis=1).sum()
    return BuiltGraph(loss, {"x": x}, {"x": x.data.copy()})


def build_div_exp_log() -> BuiltGraph:
    rng = _seeded("div_exp_log")
    x = Tensor(np.abs(rng.normal(size=(3,))) + 0.7, requires_grad=True)
    y = Tensor(np.abs(rng.normal(size=(3,))) + 0.7, requires_grad=True)
    loss = (ops.exp(-x) + ops.log(y) + x / y).sum()
    return BuiltGraph(loss, {"x": x, "y": y},
                      {"x": x.data.copy(), "y": y.data.copy()})


def build_batched_matmul() -> BuiltGraph:
    rng = _seeded("batched_matmul")
    a = Tensor(rng.normal(size=(2, 1, 3)) * 0.4, requires_grad=True)
    b = Tensor(rng.normal(size=(1, 3, 4)) * 0.4, requires_grad=True)
    loss = ops.matmul(a, b).sum()
    return BuiltGraph(loss, {"a": a, "b": b},
                      {"a": a.data.copy(), "b": b.data.copy()})


SCENARIOS: Dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario("broadcast_add",
                 "row + column + elementwise broadcasting, squared sum",
                 build_broadcast_add, ref_broadcast_add),
        Scenario("matmul_mlp",
                 "matmul + bias + sigmoid, scalar sum",
                 build_matmul_mlp, ref_matmul_mlp),
        Scenario("shared_branch",
                 "one intermediate consumed by two branches (gradient accumulation)",
                 build_shared_branch, ref_shared_branch),
        Scenario("activations",
                 "relu * sigmoid + tanh composition",
                 build_activations, ref_activations),
        Scenario("reduction",
                 "mean over axis and global mean combined",
                 build_reduction, ref_reduction),
        Scenario("empty_matmul",
                 "matmul over an empty contraction dimension",
                 build_empty_matmul, ref_empty_matmul),
        Scenario("empty_mean",
                 "mean reduction over an empty axis",
                 build_empty_mean, ref_empty_mean),
        Scenario("div_exp_log",
                 "division, exp(-x) and log composition",
                 build_div_exp_log, ref_div_exp_log),
        Scenario("batched_matmul",
                 "broadcasting batched matmul (2,1,3)x(1,3,4)",
                 build_batched_matmul, ref_batched_matmul),
    ]
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise KeyError(
            f"unknown scenario {name!r}; available: {sorted(SCENARIOS)}"
        ) from None
