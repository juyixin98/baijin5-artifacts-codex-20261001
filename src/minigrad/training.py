"""Training state: grad mode, parameters, and a minimal optimizer.

Grad mode is thread-local. Ops record graph nodes only while grad mode is
enabled (see ``ops._record``); ``no_grad()`` therefore gives real inference
semantics rather than a post-hoc flag.
"""

from __future__ import annotations

import threading

from .tensor import Tensor

_state = threading.local()


def is_grad_enabled() -> bool:
    return getattr(_state, "grad_enabled", True)


class _GradMode:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._previous = None

    def __enter__(self):
        self._previous = is_grad_enabled()
        _state.grad_enabled = self.enabled
        return self

    def __exit__(self, exc_type, exc, tb):
        _state.grad_enabled = self._previous
        return False


def no_grad() -> _GradMode:
    """Context manager: ops inside do not record the computation graph."""
    return _GradMode(False)


def enable_grad() -> _GradMode:
    """Context manager: re-enable recording (e.g. nested inside no_grad)."""
    return _GradMode(True)


class Parameter(Tensor):
    """A leaf tensor that requires grad by default (a trainable weight)."""

    def __init__(self, data, name: str | None = None):
        super().__init__(data, requires_grad=True, name=name)


class SGD:
    """Stochastic gradient descent over a list of parameters.

    ``step`` mutates parameter data in place and therefore bumps each
    parameter's version counter — a parameter updated between forward and
    backward is treated like any other in-place modification and will be
    rejected by version checking if a pending graph depends on it.
    """

    def __init__(self, params, lr: float):
        if lr <= 0:
            raise ValueError(f"learning rate must be positive, got {lr}")
        self.params = list(params)
        self.lr = float(lr)

    def step(self) -> None:
        for param in self.params:
            if param.grad is None:
                continue  # no gradient flowed: leave the parameter untouched
            param.data -= self.lr * param.grad
            param._bump()  # direct ndarray write: record the mutation explicitly

    def zero_grad(self) -> None:
        for param in self.params:
            param.zero_grad()
