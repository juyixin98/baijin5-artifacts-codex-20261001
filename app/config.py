"""Service configuration.

All resource limits live here so that "resource exhausted" failures are
explicit, testable policy decisions rather than incidental crashes.
Values can be overridden through environment variables prefixed with
``FIR_`` (e.g. ``FIR_MAX_SAMPLES=1000``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    # Maximum accepted samples per signal (per one-shot request, and total
    # accumulated per stream session).
    max_samples: int = 200_000
    # Maximum FIR model order (number of taps).
    max_order: int = 512
    # Maximum lag searched when delay estimation is requested.
    max_delay: int = 10_000
    # Budget on design-matrix cells (n_rows * order) to bound memory.
    max_matrix_cells: int = 20_000_000
    # Default Tikhonov regularization strength when the caller passes none.
    default_regularization: float = 1e-6
    # Directory for JSONL run logs; None disables file logging.
    log_dir: str | None = "logs"

    @classmethod
    def from_env(cls) -> "AppConfig":
        def _get(name: str, default: str | None) -> str | None:
            return os.environ.get(f"FIR_{name}", default)

        log_dir = _get("LOG_DIR", cls.log_dir)
        return cls(
            max_samples=int(_get("MAX_SAMPLES", str(cls.max_samples))),
            max_order=int(_get("MAX_ORDER", str(cls.max_order))),
            max_delay=int(_get("MAX_DELAY", str(cls.max_delay))),
            max_matrix_cells=int(_get("MAX_MATRIX_CELLS", str(cls.max_matrix_cells))),
            default_regularization=float(
                _get("DEFAULT_REGULARIZATION", str(cls.default_regularization))
            ),
            log_dir=None if log_dir in (None, "", "none") else log_dir,
        )
