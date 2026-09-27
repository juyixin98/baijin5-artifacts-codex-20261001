"""Tensor type.

A ``Tensor`` wraps a NumPy array with autodiff metadata:

* ``requires_grad`` — whether ops on this tensor record graph nodes.
* ``grad`` — ``None`` means "no gradient has flowed to this tensor"
  (unreachable from the loss, or backward not run); an all-zeros array is a
  real, computed zero gradient. The two are never conflated.
* ``_version`` — bumped by every in-place mutation performed through the
  Tensor API. Graph nodes snapshot the versions of the tensors they save and
  reject backward if a saved tensor has since been mutated.

Boundary: mutating the raw ``.data`` ndarray directly bypasses version
tracking (same class of caveat as PyTorch's ``.data``). Use the Tensor API
(``__setitem__``, ``fill_``, ``copy_``, ``zero_``, ``__iadd__`` …) for
mutations that must be tracked. See docs/semantics.md.
"""

from __future__ import annotations

import numpy as np

from .config import get_settings
from .errors import BackwardError, NonScalarBackwardError
from .graph import run_backward


class Tensor:
    __array_priority__ = 1000  # win against numpy scalars in mixed ops

    def __init__(self, data, requires_grad: bool = False, name: str | None = None,
                 *, _node=None):
        dtype = np.dtype(get_settings().dtype)
        if isinstance(data, Tensor):
            data = data.data
        self.data = np.asarray(data, dtype=dtype)
        self.requires_grad = bool(requires_grad)
        self.grad: np.ndarray | None = None
        self.name = name
        self._version = 0
        self._node = _node

    # -- basic views -----------------------------------------------------
    @property
    def shape(self) -> tuple:
        return self.data.shape

    @property
    def ndim(self) -> int:
        return self.data.ndim

    @property
    def size(self) -> int:
        return self.data.size

    @property
    def dtype(self) -> np.dtype:
        return self.data.dtype

    def numpy(self) -> np.ndarray:
        """A copy of the underlying data (mutating it cannot corrupt state)."""
        return self.data.copy()

    def __repr__(self) -> str:
        return (
            f"Tensor(shape={self.data.shape}, dtype={self.data.dtype}, "
            f"requires_grad={self.requires_grad}, name={self.name!r})"
        )

    # -- in-place mutation API (version-tracked) --------------------------
    def _bump(self) -> None:
        self._version += 1

    def __setitem__(self, index, value) -> None:
        if isinstance(value, Tensor):
            value = value.data
        self.data[index] = value
        self._bump()

    def fill_(self, value) -> "Tensor":
        self.data[...] = value
        self._bump()
        return self

    def copy_(self, other) -> "Tensor":
        source = other.data if isinstance(other, Tensor) else np.asarray(other)
        self.data[...] = source
        self._bump()
        return self

    def zero_(self) -> "Tensor":
        self.data[...] = 0
        self._bump()
        return self

    def __iadd__(self, other):
        self.data[...] = self.data + _as_data(other)
        self._bump()
        return self

    def __isub__(self, other):
        self.data[...] = self.data - _as_data(other)
        self._bump()
        return self

    def __imul__(self, other):
        self.data[...] = self.data * _as_data(other)
        self._bump()
        return self

    def __itruediv__(self, other):
        self.data[...] = self.data / _as_data(other)
        self._bump()
        return self

    # -- autodiff ----------------------------------------------------------
    def backward(self, grad=None, retain_graph: bool = False) -> None:
        if not self.requires_grad:
            raise BackwardError(
                f"backward() called on tensor {self.name!r} with "
                "requires_grad=False; no graph was recorded"
            )
        if grad is None:
            if self.data.size != 1:
                raise NonScalarBackwardError(self.data.shape)
            grad = np.ones_like(self.data)
        run_backward(self, np.asarray(grad, dtype=self.data.dtype), retain_graph)

    def zero_grad(self) -> None:
        """Reset to the 'no gradient' state (None), not to zeros."""
        self.grad = None

    def detach(self) -> "Tensor":
        """A new Tensor sharing the same data, cut off from the graph."""
        return Tensor(self.data, requires_grad=False, name=self.name)

    # -- operator sugar (implemented in ops.py) -----------------------------
    def __add__(self, other):
        from . import ops
        return ops.add(self, other)

    def __radd__(self, other):
        from . import ops
        return ops.add(other, self)

    def __sub__(self, other):
        from . import ops
        return ops.sub(self, other)

    def __rsub__(self, other):
        from . import ops
        return ops.sub(other, self)

    def __mul__(self, other):
        from . import ops
        return ops.mul(self, other)

    def __rmul__(self, other):
        from . import ops
        return ops.mul(other, self)

    def __truediv__(self, other):
        from . import ops
        return ops.div(self, other)

    def __rtruediv__(self, other):
        from . import ops
        return ops.div(other, self)

    def __neg__(self):
        from . import ops
        return ops.neg(self)

    def __pow__(self, other):
        from . import ops
        return ops.pow(self, other)

    def __matmul__(self, other):
        from . import ops
        return ops.matmul(self, other)

    def matmul(self, other):
        from . import ops
        return ops.matmul(self, other)

    def sum(self, axis=None, keepdims: bool = False):
        from . import ops
        return ops.sum(self, axis=axis, keepdims=keepdims)

    def mean(self, axis=None, keepdims: bool = False):
        from . import ops
        return ops.mean(self, axis=axis, keepdims=keepdims)

    def max(self, axis=None, keepdims: bool = False):
        from . import ops
        return ops.max(self, axis=axis, keepdims=keepdims)

    def reshape(self, shape):
        from . import ops
        return ops.reshape(self, shape)

    def transpose(self, axes=None):
        from . import ops
        return ops.transpose(self, axes)

    @property
    def T(self):
        return self.transpose()


def _as_data(value):
    return value.data if isinstance(value, Tensor) else value
