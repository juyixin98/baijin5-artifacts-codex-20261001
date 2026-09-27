"""Operator definitions: forward computations and vector-Jacobian products.

Every op is a :class:`Node` capturing:

* its input :class:`~autodiff.tensor.Tensor` s and their *versions* at capture
  time (consumed by the graph engine's stale-graph detection),
* whatever constant-sized intermediates its VJP needs.

Broadcasting rules (acceptance rule 1):

* Binary ops broadcast forward with NumPy semantics.
* Backward gradients are *unbroadcast*: axes inserted for broadcasting are
  summed, and dimensions of size 1 are summed with ``keepdims`` then reshaped
  back.  Empty axes sum to zero naturally.
* A tensor consumed by several downstream branches accumulates each
  contribution (addition), in both the engine's internal table and leaf
  ``.grad``.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from .config import EMPTY_REDUCTION_GRAD
from .tensor import Tensor

# ---------------------------------------------------------------------------
# Broadcasting helpers
# ---------------------------------------------------------------------------


def unbroadcast(grad: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Inverse of ``np.broadcast_to`` for a cotangent.

    *grad* has the broadcast (output) shape; reduce it back to *shape*.
    """
    # Scalar target: reduce everything.
    if len(shape) == 0:
        reduced = grad.sum()
        return np.asarray(reduced, dtype=grad.dtype).reshape(())
    # Leading axes inserted by broadcasting: sum and drop.
    while grad.ndim > len(shape):
        grad = grad.sum(axis=0)
    # Dimensions broadcast from size 1: sum with the axis retained.
    for axis, dim in enumerate(shape):
        if dim == 1 and grad.shape[axis] != 1:
            grad = grad.sum(axis=axis, keepdims=True)
    return grad.reshape(shape)


# ---------------------------------------------------------------------------
# Node base
# ---------------------------------------------------------------------------


class Node:
    """One differentiable operation in the graph."""

    def __init__(self, inputs: Sequence[Tensor], output: Tensor) -> None:
        self.inputs: list[Tensor] = list(inputs)
        self.output: Tensor = output
        # Snapshot versions for stale-graph detection.
        self.input_versions: list[int] = [t._version for t in self.inputs]
        self._released = False

    # -- introspection ------------------------------------------------------
    def is_stale(self) -> list[tuple[int, int, int]]:
        """Return ``(input_index, captured_version, current_version)`` mismatches."""
        mismatches: list[tuple[int, int, int]] = []
        for i, t in enumerate(self.inputs):
            if t._version != self.input_versions[i]:
                mismatches.append((i, self.input_versions[i], t._version))
        return mismatches

    # -- differentiation ----------------------------------------------------
    def backward(self, grad_output: np.ndarray) -> list[Optional[np.ndarray]]:
        """Vector-Jacobian product. One entry per input (None = no gradient)."""
        raise NotImplementedError

    # -- memory: release / retain ------------------------------------------
    def release(self) -> None:
        """Drop references held solely for backward (graph release strategy)."""
        self._released = True
        self.inputs = []
        self.output = None  # type: ignore[assignment]

    def _check_alive(self) -> None:
        if self._released:
            from .graph import GraphReleasedError

            raise GraphReleasedError(
                "graph was released after a previous backward() without "
                "retain_graph=True"
            )


# ---------------------------------------------------------------------------
# Binary elementwise ops with broadcasting
# ---------------------------------------------------------------------------


class Add(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        a, b = self.inputs
        return [unbroadcast(g, a.shape), unbroadcast(g, b.shape)]


class Sub(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        a, b = self.inputs
        return [unbroadcast(g, a.shape), unbroadcast(-g, b.shape)]


class Mul(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        a, b = self.inputs
        ga = unbroadcast(g * b.data, a.shape)
        gb = unbroadcast(g * a.data, b.shape)
        return [ga, gb]


class Div(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        a, b = self.inputs
        ga = unbroadcast(g / b.data, a.shape)
        gb = unbroadcast(-g * a.data / (b.data ** 2), b.shape)
        return [ga, gb]


class Neg(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        return [-g]


# ---------------------------------------------------------------------------
# Math / activations
# ---------------------------------------------------------------------------


class Exp(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        # output = exp(x); d/dx = output
        return [g * self.output.data]


class Log(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        (x,) = self.inputs
        return [g / x.data]


class ReLU(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        (x,) = self.inputs
        return [g * (x.data > 0)]


class Sigmoid(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        s = self.output.data
        return [g * s * (1.0 - s)]


class Tanh(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        t = self.output.data
        return [g * (1.0 - t * t)]


# ---------------------------------------------------------------------------
# Matrix multiplication (supports broadcasting batch dims)
# ---------------------------------------------------------------------------


class MatMul(Node):
    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        a, b = self.inputs
        ad, bd = a.data, b.data
        # NumPy matmul treats 1-D operands specially:
        #   (K,) @ (K,N) -> (N,);  (M,K) @ (K,) -> (M,);  (K,)@(K,) -> ()
        if ad.ndim == 1 and bd.ndim >= 2:
            # Per-batch: ga accumulates g @ b^T; gb holds outer(a, g_batch).
            ga_full = np.matmul(g, np.swapaxes(bd, -1, -2))  # (..., K)
            ga = ga_full.sum(axis=tuple(range(ga_full.ndim - 1)))
            gb = np.matmul(ad[:, None], np.expand_dims(g, -2))  # (..., K, N)
        elif ad.ndim >= 2 and bd.ndim == 1:
            gb_full = np.matmul(np.swapaxes(ad, -1, -2), g)  # (..., K)
            gb = gb_full.sum(axis=tuple(range(gb_full.ndim - 1)))
            ga = np.matmul(np.expand_dims(g, -1), bd[None, :])  # (..., M, K)
        elif ad.ndim == 1 and bd.ndim == 1:
            ga = g * bd
            gb = g * ad
        else:
            # >=2-D operands: empty contraction dimensions sum to zero
            # naturally, yielding correct zero cotangents there.
            ga = np.matmul(g, np.swapaxes(bd, -1, -2))
            gb = np.matmul(np.swapaxes(ad, -1, -2), g)
        return [unbroadcast(ga, a.shape), unbroadcast(gb, b.shape)]


# ---------------------------------------------------------------------------
# Reductions
# ---------------------------------------------------------------------------


def _axis_tuple(ndim: int, axis: Optional[int | Sequence[int]]) -> tuple[int, ...]:
    if axis is None:
        return tuple(range(ndim))
    if isinstance(axis, int):
        axis = (axis,)
    return tuple(ax % ndim for ax in axis)


def _reduce_keepdims(x: np.ndarray, axis: tuple[int, ...], keepdims: bool) -> np.ndarray:
    g = x
    if not keepdims:
        # Re-insert one reduced axis at a time for broadcasting back.
        for ax in sorted(axis):
            g = np.expand_dims(g, ax)
    return g


class Sum(Node):
    def __init__(self, inputs: Sequence[Tensor], output: Tensor,
                 axis: tuple[int, ...], keepdims: bool) -> None:
        super().__init__(inputs, output)
        self.axis = axis
        self.keepdims = keepdims

    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        (x,) = self.inputs
        g_full = _reduce_keepdims(g, self.axis, self.keepdims)
        return [np.broadcast_to(g_full, x.shape).copy()]


class Mean(Node):
    def __init__(self, inputs: Sequence[Tensor], output: Tensor,
                 axis: tuple[int, ...], keepdims: bool, denom: int) -> None:
        super().__init__(inputs, output)
        self.axis = axis
        self.keepdims = keepdims
        self.denom = denom

    def backward(self, g: np.ndarray) -> list[Optional[np.ndarray]]:
        self._check_alive()
        (x,) = self.inputs
        g_full = _reduce_keepdims(g, self.axis, self.keepdims)
        if self.denom == 0:
            # Empty reduction: 0/0 is undefined algebraically; the configured
            # policy is an explicit zero cotangent (not "no gradient").
            return [np.full(x.shape, EMPTY_REDUCTION_GRAD, dtype=x.data.dtype)]
        return [np.broadcast_to(g_full / self.denom, x.shape).copy()]


# ---------------------------------------------------------------------------
# Functional API
# ---------------------------------------------------------------------------


def _finish(node: Node, out_data: np.ndarray) -> Tensor:
    """Wrap forward output, wire the node when any input requires grad."""
    tracked = any(t.requires_grad for t in node.inputs)
    out = Tensor(out_data, requires_grad=tracked)
    if tracked:
        from .config import get_config

        if get_config().grad_enabled:
            # An output of a previously released graph cannot be re-used as a
            # differentiable input: its history is gone, so differentiating
            # the new graph would silently truncate the chain.  Reject it at
            # capture time (use .detach() to opt in to treating it as a leaf).
            for t in node.inputs:
                if t.is_graph_detached():
                    from .graph import GraphReleasedError

                    raise GraphReleasedError(
                        "cannot build a new differentiable op from a tensor "
                        "whose graph was released by an earlier backward(); "
                        "call .detach() explicitly to use it as a constant leaf"
                    )
            node.output = out
            out._node = node
            for t in node.inputs:
                t.mark_used_in_graph()
    return out


def add(a: Tensor, b: Tensor) -> Tensor:
    out = np.add(a.data, b.data)
    return _finish(Add([a, b], None), out)  # type: ignore[arg-type]


def sub(a: Tensor, b: Tensor) -> Tensor:
    return _finish(Sub([a, b], None), np.subtract(a.data, b.data))  # type: ignore[arg-type]


def mul(a: Tensor, b: Tensor) -> Tensor:
    return _finish(Mul([a, b], None), np.multiply(a.data, b.data))  # type: ignore[arg-type]


def div(a: Tensor, b: Tensor) -> Tensor:
    return _finish(Div([a, b], None), np.true_divide(a.data, b.data))  # type: ignore[arg-type]


def neg(a: Tensor) -> Tensor:
    return _finish(Neg([a], None), np.negative(a.data))  # type: ignore[arg-type]


def exp(a: Tensor) -> Tensor:
    return _finish(Exp([a], None), np.exp(a.data))  # type: ignore[arg-type]


def log(a: Tensor) -> Tensor:
    return _finish(Log([a], None), np.log(a.data))  # type: ignore[arg-type]


def relu(a: Tensor) -> Tensor:
    return _finish(ReLU([a], None), np.maximum(a.data, 0.0))  # type: ignore[arg-type]


def sigmoid(a: Tensor) -> Tensor:
    return _finish(Sigmoid([a], None), 1.0 / (1.0 + np.exp(-a.data)))  # type: ignore[arg-type]


def tanh(a: Tensor) -> Tensor:
    return _finish(Tanh([a], None), np.tanh(a.data))  # type: ignore[arg-type]


def matmul(a: Tensor, b: Tensor) -> Tensor:
    return _finish(MatMul([a, b], None), np.matmul(a.data, b.data))  # type: ignore[arg-type]


def sum_(a: Tensor, axis: Optional[int | Sequence[int]] = None,
         keepdims: bool = False) -> Tensor:
    ax = _axis_tuple(a.ndim, axis)
    out = a.data.sum(axis=ax if ax != () else None, keepdims=keepdims)
    return _finish(Sum([a], None, ax, keepdims), out)  # type: ignore[arg-type]


def mean(a: Tensor, axis: Optional[int | Sequence[int]] = None,
         keepdims: bool = False) -> Tensor:
    ax = _axis_tuple(a.ndim, axis)
    # Number of elements collapsed by the reduction (product of reduced dims);
    # zero exactly when the reduction runs over an empty dimension.
    denom = int(np.prod([a.shape[i] for i in ax])) if ax else 1
    if denom == 0:
        # numpy warns / returns nan for mean over empty; follow the explicit
        # empty policy and produce a defined zero output.
        if keepdims:
            out_shape = tuple(1 if i in ax else d for i, d in enumerate(a.shape))
            out = np.full(out_shape, EMPTY_REDUCTION_GRAD)
        else:
            out_shape = tuple(d for i, d in enumerate(a.shape) if i not in ax)
            out = np.full(out_shape, EMPTY_REDUCTION_GRAD)
    else:
        out = a.data.mean(axis=ax if ax != () else None, keepdims=keepdims)
    return _finish(Mean([a], None, ax, keepdims, denom), out)  # type: ignore[arg-type]
