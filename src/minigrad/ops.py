"""Differentiable operators.

Broadcasting rule (acceptance requirement #1): when an op broadcasts an
operand, the backward pass sums the upstream gradient over exactly the axes
that were expanded (leading axes first, then size-1 axes), so the returned
gradient always has the operand's original shape. ``unbroadcast`` implements
this once and every binary op uses it.

Version-detection rule (acceptance requirement #2): an op *saves* an input
tensor only when its backward closure reads that input's values. Saved
tensors are version-checked at backward time; ops that do not save (add,
sub, sum, …) are unaffected by later in-place writes to their inputs.
"""

from __future__ import annotations

import numpy as np

from .graph import Node
from .tensor import Tensor
from .training import is_grad_enabled


def ensure_tensor(value) -> Tensor:
    if isinstance(value, Tensor):
        return value
    return Tensor(value)


def unbroadcast(grad: np.ndarray, shape: tuple) -> np.ndarray:
    """Sum ``grad`` down to ``shape``, reversing NumPy broadcasting."""
    grad = np.asarray(grad)
    while grad.ndim > len(shape):
        grad = grad.sum(axis=0)
    for axis, dim in enumerate(shape):
        if dim == 1 and grad.shape[axis] != 1:
            grad = grad.sum(axis=axis, keepdims=True)
    return grad.reshape(shape)


def _record(op_name, inputs, out_data, backward_fn, saved=()) -> Tensor:
    """Wrap a forward result, recording a graph node when grad is enabled."""
    inputs = tuple(inputs)
    if is_grad_enabled() and any(t.requires_grad for t in inputs):
        node = Node(op_name, inputs, backward_fn, saved)
        out = Tensor(out_data, requires_grad=True, _node=node)
        node.out = out
        return out
    return Tensor(out_data)


def _normalize_axes(axis, ndim: int):
    if axis is None:
        return None
    axes = (axis,) if isinstance(axis, (int, np.integer)) else tuple(axis)
    return tuple(int(a) % ndim for a in axes)


# --------------------------------------------------------------------------
# elementwise binary ops
# --------------------------------------------------------------------------

def add(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)

    def backward(g):
        return unbroadcast(g, a.shape), unbroadcast(g, b.shape)

    return _record("add", (a, b), a.data + b.data, backward)


def sub(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)

    def backward(g):
        return unbroadcast(g, a.shape), unbroadcast(-g, b.shape)

    return _record("sub", (a, b), a.data - b.data, backward)


def mul(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)

    def backward(g):
        return (
            unbroadcast(g * b.data, a.shape),
            unbroadcast(g * a.data, b.shape),
        )

    return _record("mul", (a, b), a.data * b.data, backward, saved=(a, b))


def div(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)

    def backward(g):
        return (
            unbroadcast(g / b.data, a.shape),
            unbroadcast(-g * a.data / b.data ** 2, b.shape),
        )

    return _record("div", (a, b), a.data / b.data, backward, saved=(a, b))


def pow(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)
    out = a.data ** b.data

    def backward(g):
        grad_a = g * b.data * a.data ** (b.data - 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            # d/db = out * ln(a); NaN for a <= 0 (documented boundary)
            grad_b = g * out * np.log(a.data)
        return unbroadcast(grad_a, a.shape), unbroadcast(grad_b, b.shape)

    return _record("pow", (a, b), out, backward, saved=(a, b))


def neg(a) -> Tensor:
    a = ensure_tensor(a)

    def backward(g):
        return (-g,)

    return _record("neg", (a,), -a.data, backward)


# --------------------------------------------------------------------------
# matmul (1D / 2D / batched, following np.matmul semantics)
# --------------------------------------------------------------------------

def matmul(a, b) -> Tensor:
    a, b = ensure_tensor(a), ensure_tensor(b)

    def backward(g):
        a_vec, b_vec = a.ndim == 1, b.ndim == 1
        a_mat = np.expand_dims(a.data, 0) if a_vec else a.data
        b_mat = np.expand_dims(b.data, -1) if b_vec else b.data
        if a_vec and b_vec:
            g_mat = g.reshape(1, 1)
        elif a_vec:
            g_mat = np.expand_dims(g, -2)
        elif b_vec:
            g_mat = np.expand_dims(g, -1)
        else:
            g_mat = g
        grad_a = np.matmul(g_mat, np.swapaxes(b_mat, -1, -2))
        grad_b = np.matmul(np.swapaxes(a_mat, -1, -2), g_mat)
        if a_vec:
            grad_a = np.squeeze(grad_a, axis=-2)
        if b_vec:
            grad_b = np.squeeze(grad_b, axis=-1)
        return unbroadcast(grad_a, a.shape), unbroadcast(grad_b, b.shape)

    return _record("matmul", (a, b), np.matmul(a.data, b.data), backward,
                   saved=(a, b))


# --------------------------------------------------------------------------
# reductions
# --------------------------------------------------------------------------

def sum(a, axis=None, keepdims: bool = False) -> Tensor:
    a = ensure_tensor(a)
    axes = _normalize_axes(axis, a.ndim)
    out = a.data.sum(axis=axes, keepdims=keepdims)

    def backward(g):
        upstream = g
        if axes is not None and not keepdims:
            upstream = np.expand_dims(g, axes)
        return (np.broadcast_to(upstream, a.shape).copy(),)

    return _record("sum", (a,), out, backward)


def mean(a, axis=None, keepdims: bool = False) -> Tensor:
    a = ensure_tensor(a)
    axes = _normalize_axes(axis, a.ndim)
    if axes is None:
        count = a.data.size
    else:
        count = int(np.prod([a.shape[i] for i in axes]))
    out = a.data.mean(axis=axes, keepdims=keepdims)

    def backward(g):
        upstream = g
        if axes is not None and not keepdims:
            upstream = np.expand_dims(g, axes)
        # count == 0 (empty reduction) yields inf/NaN, matching NumPy
        return (np.broadcast_to(upstream, a.shape).copy() / count,)

    return _record("mean", (a,), out, backward)


def max(a, axis=None, keepdims: bool = False) -> Tensor:
    a = ensure_tensor(a)
    axes = _normalize_axes(axis, a.ndim)
    kept = a.data.max(axis=axes, keepdims=True)
    # ties split the gradient evenly; captured at forward time
    mask = a.data == kept
    route = mask / mask.sum(axis=axes, keepdims=True)
    if keepdims:
        out = kept
    elif axes is None:
        out = kept.reshape(())
    else:
        out = np.squeeze(kept, axis=axes)

    def backward(g):
        upstream = g
        if not keepdims and axes is not None:
            upstream = np.expand_dims(g, axes)
        return (route * upstream,)

    return _record("max", (a,), out, backward)


# --------------------------------------------------------------------------
# activations / elementwise unary
# --------------------------------------------------------------------------

def exp(a) -> Tensor:
    a = ensure_tensor(a)
    out = np.exp(a.data)

    def backward(g):
        return (g * out,)

    return _record("exp", (a,), out, backward)


def log(a) -> Tensor:
    a = ensure_tensor(a)

    def backward(g):
        return (g / a.data,)

    return _record("log", (a,), np.log(a.data), backward, saved=(a,))


def sigmoid(a) -> Tensor:
    a = ensure_tensor(a)
    out = 1.0 / (1.0 + np.exp(-a.data))

    def backward(g):
        return (g * out * (1.0 - out),)

    return _record("sigmoid", (a,), out, backward)


def tanh(a) -> Tensor:
    a = ensure_tensor(a)
    out = np.tanh(a.data)

    def backward(g):
        return (g * (1.0 - out ** 2),)

    return _record("tanh", (a,), out, backward)


def relu(a) -> Tensor:
    a = ensure_tensor(a)
    mask = a.data > 0

    def backward(g):
        return (g * mask,)

    return _record("relu", (a,), a.data * mask, backward)


# --------------------------------------------------------------------------
# shape ops
# --------------------------------------------------------------------------

def reshape(a, shape) -> Tensor:
    a = ensure_tensor(a)
    shape = tuple(shape)

    def backward(g):
        return (g.reshape(a.shape),)

    return _record("reshape", (a,), a.data.reshape(shape), backward)


def transpose(a, axes=None) -> Tensor:
    a = ensure_tensor(a)
    if axes is None:
        axes = tuple(reversed(range(a.ndim)))
    axes = tuple(int(ax) % a.ndim for ax in axes)
    inverse = tuple(np.argsort(axes))

    def backward(g):
        return (np.transpose(g, inverse),)

    return _record("transpose", (a,), np.transpose(a.data, axes), backward)


def broadcast_to(a, shape) -> Tensor:
    a = ensure_tensor(a)
    shape = tuple(shape)

    def backward(g):
        return (unbroadcast(g, a.shape),)

    return _record("broadcast_to", (a,), np.broadcast_to(a.data, shape).copy(),
                   backward)
