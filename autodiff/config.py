"""Independent configuration for the autodiff backend.

Everything tunable lives here; core modules never read environment variables
directly. Config is immutable (frozen dataclass) and a process-wide default is
provided through :func:`get_config`.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, replace
from typing import Any

# Numerical verification defaults.  Centralised so tests and the CLI share one
# definition of "close enough".
DEFAULT_FD_EPS: float = 1e-3
DEFAULT_FD_ATOL: float = 1e-5
DEFAULT_FD_RTOL: float = 5e-2
DEFAULT_GRAD_ATOL: float = 1e-8
DEFAULT_GRAD_RTOL: float = 1e-6

# Empty-dimension reductions: NaNs are produced for 0/0; we instead define the
# derivative over empty axes to be an explicit zero (0.0), mirroring
# numpy.sum semantics for values while keeping gradients finite.
EMPTY_REDUCTION_GRAD: float = 0.0

# Diagnostics: values printed by diagnostics never exceed this many array
# elements (sensitive/long payloads are truncated / redacted).
DIAG_MAX_ELEMENTS: int = 8

_TRUEISH = {"1", "true", "yes", "on"}


def _as_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUEISH


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Immutable; obtain a copy via :meth:`with_`."""

    # When False, new Tensors built from the same data are detached from any
    # graph (requires_grad forced False).  The graph also drops intermediates
    # whenever no live Tensor references them (see graph.py retain strategy).
    grad_enabled: bool = True
    # Diagnostics verbosity.
    log_diagnostics: bool = True
    # Finite-difference checking parameters.
    fd_eps: float = DEFAULT_FD_EPS
    fd_atol: float = DEFAULT_FD_ATOL
    fd_rtol: float = DEFAULT_FD_RTOL
    # Exact (autodiff-vs-autodiff) comparison tolerances.
    grad_atol: float = DEFAULT_GRAD_ATOL
    grad_rtol: float = DEFAULT_GRAD_RTOL
    # How many top-level ops a backward pass may traverse (cycle guard).
    max_backward_nodes: int = 1_000_000
    # Extra structured key/values attached to every diagnostic record.
    diagnostic_context: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def with_(self, **changes: Any) -> "Config":
        """Return a copy with selected fields replaced (immutable update)."""
        return replace(self, **changes)


_lock = threading.Lock()
_config = Config(
    grad_enabled=_as_bool("AUTODIFF_GRAD_ENABLED", True),
    log_diagnostics=_as_bool("AUTODIFF_LOG_DIAGNOSTICS", True),
)


def get_config() -> Config:
    """Return the active process-wide configuration."""
    return _config


def set_config(config: Config) -> None:
    """Replace the process-wide configuration atomically."""
    global _config
    with _lock:
        _config = config


class no_grad:
    """Context manager that disables graph construction inside its block.

    Both a context manager and a decorator.  Config itself is immutable; we
    temporarily swap the process config and restore it on exit.
    """

    def __enter__(self) -> Config:
        self._prev = get_config()
        set_config(self._prev.with_(grad_enabled=False))
        return self._prev

    def __exit__(self, exc_type, exc, tb) -> None:
        set_config(self._prev)

    def __call__(self, fn):
        def wrapper(*args, **kwargs):
            with self:
                return fn(*args, **kwargs)

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper
