"""Tensor data type.

A :class:`Tensor` wraps a *read-only* float64 NumPy array plus autodiff
metadata:

* ``requires_grad`` - whether the tensor participates in gradient tracking.
* ``grad`` - ``None`` means **no gradient** (disconnected / never reached); a
  zero ``ndarray`` means a **computed zero gradient**.  The two are never
  conflated.
* ``_version`` - bumped by every *sanctioned* in-place data replacement.  The
  graph snapshots the version when a tensor is captured as an op input and
  rejects backward if it changed (see :mod:`autodiff.graph`).

Raw ``tensor.data[...] = ...`` writes are blocked because the wrapped array is
made read-only; sanctioned mutation goes through :meth:`Tensor.set_data`,
which performs version bookkeeping.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence, Union

import numpy as np

from .config import get_config

ArrayLike = Union[np.ndarray, Sequence[Any], float, int, bool]

# Internal floating point work precision.
_DEFAULT_DTYPE = np.float64


def _as_locked_array(data: ArrayLike, dtype: np.dtype = _DEFAULT_DTYPE) -> np.ndarray:
    """Convert *data* to a contiguous, read-only array.

    Read-only storage is the first line of in-place mutation defence: a stray
    ``t.data[0] = 1`` raises immediately instead of silently corrupting the
    graph.  :meth:`Tensor.set_data` is the only supported mutation path and it
    replaces (never writes through) the array.
    """
    arr = np.array(data, dtype=dtype, order="C", copy=True)
    arr.setflags(write=False)
    return arr


class TensorError(RuntimeError):
    """Base class for tensor-related failures."""


class Tensor:
    """A tracked multi-dimensional array."""

    __slots__ = ("_data", "requires_grad", "grad", "_version", "_used_in_graph",
                 "_node", "_graph_detached")

    def __init__(
        self,
        data: ArrayLike,
        requires_grad: bool = False,
        dtype: np.dtype = _DEFAULT_DTYPE,
    ) -> None:
        self._data = _as_locked_array(data, dtype)
        self.requires_grad = bool(requires_grad) and get_config().grad_enabled
        # None  == no gradient (never computed / disconnected)
        # array == a gradient value, which may legitimately be all zeros
        self.grad: Optional[np.ndarray] = None
        self._version = 0
        self._used_in_graph = False
        # Populated by the graph engine when this tensor is an op output.
        # Weak ownership is avoided for simplicity; nodes drop their
        # references on graph release (see graph.release_strategy docs).
        self._node: Optional[Any] = None
        # True once a released backward pass detached this non-leaf output
        # from its graph.  Distinguishes "real leaf" from "freed intermediate"
        # so a second backward can be rejected rather than silently seeding it.
        self._graph_detached = False

    # ------------------------------------------------------------------ data
    @property
    def data(self) -> np.ndarray:
        """Read-only view of the underlying values."""
        return self._data

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self._data.shape)

    @property
    def ndim(self) -> int:
        return self._data.ndim

    @property
    def size(self) -> int:
        return int(self._data.size)

    @property
    def dtype(self) -> np.dtype:
        return self._data.dtype

    @property
    def version(self) -> int:
        return self._version

    def is_leaf(self) -> bool:
        """True only for genuine user-created leaves.

        A non-leaf output detached by graph release has ``_node is None`` but
        is not a leaf; it is reported as detached instead.
        """
        return self._node is None and not self._graph_detached

    def is_graph_detached(self) -> bool:
        return self._graph_detached

    # --------------------------------------------------------------- mutation
    def set_data(self, data: ArrayLike) -> "Tensor":
        """Replace values in place (sanctioned in-place write).

        Always bumps the version.  If this tensor was already captured by a
        graph node, a subsequent ``backward`` rejects the graph via version
        detection rather than differentiating through stale data.
        """
        self._data = _as_locked_array(data)
        self._version += 1
        return self

    # -------------------------------------------------------------- gradients
    def zero_grad(self) -> "Tensor":
        """Set an *explicit zero* gradient (distinct from having no gradient)."""
        self.grad = np.zeros_like(self._data)
        self.grad.setflags(write=False)
        return self

    def clear_grad(self) -> "Tensor":
        """Remove the gradient entirely (back to the no-gradient state)."""
        self.grad = None
        return self

    def accumulate_grad(self, grad: np.ndarray) -> None:
        """Add *grad* into ``self.grad`` (reused-node accumulation).

        The first contribution allocates a zero buffer, so a tensor reached by
        the backward pass always ends with an array (possibly all zeros), while
        an unreached/disconnected tensor keeps ``grad is None``.
        """
        if grad.shape != self._data.shape:
            raise TensorError(
                f"gradient shape {grad.shape} does not match tensor {self._data.shape}"
            )
        if self.grad is None:
            self.grad = np.array(grad, dtype=_DEFAULT_DTYPE, copy=True, order="C")
        else:
            self.grad = self.grad + np.asarray(grad, dtype=_DEFAULT_DTYPE)
        self.grad.setflags(write=False)

    def detach(self) -> "Tensor":
        """Return a new tensor sharing the values but without graph history."""
        out = Tensor(self._data, requires_grad=False)
        return out

    # ------------------------------------------------------------------- misc
    def mark_used_in_graph(self) -> None:
        self._used_in_graph = True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        grad_state = "no-grad" if self.grad is None else f"grad(shape={self.grad.shape})"
        return (
            f"Tensor(shape={self.shape}, requires_grad={self.requires_grad}, "
            f"version={self._version}, {grad_state})"
        )

    # --------------------------------------------------------- operator sugar
    # Ops are imported lazily: ops.py imports this module at load time, so a
    # module-level import here would be circular.
    @staticmethod
    def _coerce(other: "Tensor | ArrayLike") -> "Tensor":
        return other if isinstance(other, Tensor) else Tensor(other)

    def __add__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import add
        return add(self, self._coerce(other))

    def __radd__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import add
        return add(self._coerce(other), self)

    def __sub__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import sub
        return sub(self, self._coerce(other))

    def __rsub__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import sub
        return sub(self._coerce(other), self)

    def __mul__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import mul
        return mul(self, self._coerce(other))

    def __rmul__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import mul
        return mul(self._coerce(other), self)

    def __truediv__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import div
        return div(self, self._coerce(other))

    def __rtruediv__(self, other: "Tensor | ArrayLike") -> "Tensor":
        from .ops import div
        return div(self._coerce(other), self)

    def __neg__(self) -> "Tensor":
        from .ops import neg
        return neg(self)

    def __matmul__(self, other: "Tensor") -> "Tensor":
        from .ops import matmul
        return matmul(self, self._coerce(other))

    def sum(self, axis=None, keepdims: bool = False) -> "Tensor":
        from .ops import sum_
        return sum_(self, axis=axis, keepdims=keepdims)

    def mean(self, axis=None, keepdims: bool = False) -> "Tensor":
        from .ops import mean
        return mean(self, axis=axis, keepdims=keepdims)


def tensor(data: ArrayLike, requires_grad: bool = False) -> Tensor:
    """Public factory."""
    return Tensor(data, requires_grad=requires_grad)


def zeros(shape: Sequence[int], requires_grad: bool = False) -> Tensor:
    return Tensor(np.zeros(tuple(shape), dtype=_DEFAULT_DTYPE), requires_grad)


def ones(shape: Sequence[int], requires_grad: bool = False) -> Tensor:
    return Tensor(np.ones(tuple(shape), dtype=_DEFAULT_DTYPE), requires_grad)
