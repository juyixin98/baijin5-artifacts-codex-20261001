"""Independent configuration for minigrad.

All values can be overridden with environment variables prefixed ``MINIGRAD_``.
No other module hardcodes these knobs; they read them via ``get_settings()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_PREFIX = "MINIGRAD_"


def _env(name: str, default: str) -> str:
    return os.environ.get(_ENV_PREFIX + name, default)


@dataclass(frozen=True)
class Settings:
    """Runtime and numerical-validation settings.

    dtype:               storage dtype for every Tensor (float64 keeps
                         finite-difference checks meaningful).
    fd_epsilon:          step size for central finite differences.
    fd_atol / fd_rtol:   acceptance tolerance ``atol + rtol * |numerical|``.
    undecidable_factor:  errors in ``(tol, factor * tol]`` are reported as
                         "undecidable" instead of pass/fail.
    log_level:           level for the JSON diagnostic logger.
    """

    dtype: str = "float64"
    fd_epsilon: float = 1e-6
    fd_atol: float = 1e-5
    fd_rtol: float = 1e-4
    undecidable_factor: float = 10.0
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings(
        dtype=_env("DTYPE", Settings.dtype),
        fd_epsilon=float(_env("FD_EPSILON", str(Settings.fd_epsilon))),
        fd_atol=float(_env("FD_ATOL", str(Settings.fd_atol))),
        fd_rtol=float(_env("FD_RTOL", str(Settings.fd_rtol))),
        undecidable_factor=float(
            _env("UNDECIDABLE_FACTOR", str(Settings.undecidable_factor))
        ),
        log_level=_env("LOG_LEVEL", Settings.log_level),
    )
