"""Training state: parameter containers, optimiser, train/eval mode.

Deliberately small and explicit:

* :class:`ParameterBundle` owns leaf tensors and applies gradient steps
  uniformly; parameters not reached by backward (``grad is None``) are
  *skipped with a diagnostic*, never silently treated as zero - the
  no-gradient vs zero-gradient distinction is preserved here too.
* :class:`SGD` is a plain full-batch optimiser (no momentum, no hidden state).
* :class:`TrainingState` is an explicit mode flag object so behaviour that
  should differ between training and evaluation has somewhere to read it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

import numpy as np

from .diagnostics import Diagnostics
from .tensor import Tensor


class Mode(str, Enum):
    TRAIN = "train"
    EVAL = "eval"


class TrainingState:
    """Holds the current mode and a monotonically increasing step counter."""

    def __init__(self, mode: Mode = Mode.TRAIN) -> None:
        self._mode = mode
        self._step = 0

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def step(self) -> int:
        return self._step

    def train(self) -> None:
        self._mode = Mode.TRAIN

    def eval(self) -> None:
        self._mode = Mode.EVAL

    def is_training(self) -> bool:
        return self._mode is Mode.TRAIN

    def increment_step(self) -> int:
        self._step += 1
        return self._step

    def snapshot(self) -> dict[str, object]:
        return {"mode": self._mode.value, "step": self._step}


class ParameterBundle:
    """A named, ordered collection of leaf parameter tensors."""

    def __init__(self, **params: Tensor) -> None:
        for name, t in params.items():
            if not isinstance(t, Tensor):
                raise TypeError(f"parameter {name!r} is not a Tensor")
            if not t.is_leaf():
                raise ValueError(
                    f"parameter {name!r} must be a leaf tensor (no producing op)"
                )
        self._params: dict[str, Tensor] = dict(params)

    def names(self) -> list[str]:
        return list(self._params)

    def get(self, name: str) -> Tensor:
        return self._params[name]

    def parameters(self) -> list[Tensor]:
        return list(self._params.values())

    def values(self) -> Iterable[Tensor]:
        return self._params.values()

    def zero_grads(self) -> None:
        """Reset every parameter to the *no-gradient* state (``grad=None``)."""
        for t in self._params.values():
            t.clear_grad()

    def missing_gradients(self) -> list[str]:
        """Names of parameters that backward never reached."""
        return [n for n, t in self._params.items() if t.grad is None]

    def zero_gradients(self) -> list[str]:
        """Names of parameters reached with an explicit all-zero gradient."""
        return [
            n
            for n, t in self._params.items()
            if t.grad is not None and not np.any(t.grad)
        ]


@dataclass(frozen=True)
class SGDConfig:
    lr: float = 1e-2


class SGD:
    """Vanilla stochastic gradient descent: ``p <- p - lr * p.grad``.

    Parameters with ``grad is None`` are skipped (and reported through the
    optional diagnostics) rather than updated with an implicit zero.
    """

    def __init__(
        self,
        bundle: ParameterBundle,
        config: SGDConfig = SGDConfig(),
        diagnostics: Optional[Diagnostics] = None,
    ) -> None:
        self.bundle = bundle
        self.config = config
        self.diagnostics = diagnostics

    def step(self) -> dict[str, str]:
        """Apply one update. Returns per-parameter outcome: updated/skipped."""
        outcomes: dict[str, str] = {}
        for name, p in self.bundle._params.items():
            if p.grad is None:
                outcomes[name] = "skipped-no-grad"
                if self.diagnostics is not None:
                    self.diagnostics.unable(
                        "optimizer.skip_parameter",
                        f"parameter {name!r} has no gradient; leaving values untouched",
                        parameter=name,
                        shape=list(p.shape),
                        version=p.version,
                    )
                continue
            # Immutable update: compute a fresh array, then sanction the
            # replacement (which bumps the version, invalidating any old graph).
            new_data = p.data - self.config.lr * p.grad
            p.set_data(new_data)
            outcomes[name] = "updated"
        return outcomes
