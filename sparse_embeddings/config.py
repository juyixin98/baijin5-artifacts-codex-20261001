"""Typed configuration objects with fail-fast validation.

Configuration is parsed once at table creation; an invalid configuration raises
``CONFIG_ERROR`` before any state is allocated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sparse_embeddings.tensor_types import ErrorCategory, SparseEmbeddingError

_SUPPORTED_OPTIMIZERS = ("sgd", "momentum_sgd")
_SUPPORTED_CLIP_MODES = ("global", "row")
_SUPPORTED_DTYPES = ("float32", "float64")


@dataclass(frozen=True)
class ClippingConfig:
    """Gradient clipping declaration.

    Exactly one mode is declared for the lifetime of a table:

    * ``global`` - one scalar scale derived from the norm of *all* aggregated
      touched rows together, applied uniformly.
    * ``row``    - an independent scale per touched row.

    The two modes are never mixed. A request cannot switch mode per batch; the
    service intentionally does not accept a per-request clip override.
    """

    mode: str
    max_norm: float

    def __post_init__(self) -> None:
        if self.mode not in _SUPPORTED_CLIP_MODES:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"clipping mode must be one of {_SUPPORTED_CLIP_MODES}, got {self.mode!r}",
                details={"mode": self.mode},
            )
        if not np.isfinite(self.max_norm) or self.max_norm <= 0.0:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"max_norm must be a finite positive number, got {self.max_norm!r}",
                details={"max_norm": self.max_norm},
            )


@dataclass(frozen=True)
class OptimizerConfig:
    """Optimizer declaration. ``momentum`` is only used by ``momentum_sgd``."""

    name: str = "momentum_sgd"
    learning_rate: float = 0.01
    momentum: float = 0.9

    def __post_init__(self) -> None:
        if self.name not in _SUPPORTED_OPTIMIZERS:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"optimizer must be one of {_SUPPORTED_OPTIMIZERS}, got {self.name!r}",
                details={"optimizer": self.name},
            )
        if not np.isfinite(self.learning_rate) or self.learning_rate <= 0.0:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"learning_rate must be finite and positive, got {self.learning_rate!r}",
                details={"learning_rate": self.learning_rate},
            )
        if self.name == "momentum_sgd" and not (0.0 <= self.momentum < 1.0):
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"momentum must lie in [0, 1), got {self.momentum!r}",
                details={"momentum": self.momentum},
            )


@dataclass(frozen=True)
class TableConfig:
    """Declaration of an embedding table."""

    name: str
    vocab_size: int
    dim: int
    optimizer: OptimizerConfig
    clipping: ClippingConfig | None = None
    dtype: str = "float64"
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR, "table name must be a non-empty string"
            )
        if not isinstance(self.vocab_size, int) or self.vocab_size <= 0:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"vocab_size must be a positive integer, got {self.vocab_size!r}",
            )
        if not isinstance(self.dim, int) or self.dim <= 0:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"dim must be a positive integer, got {self.dim!r}",
            )
        if self.dtype not in _SUPPORTED_DTYPES:
            raise SparseEmbeddingError(
                ErrorCategory.CONFIG_ERROR,
                f"dtype must be one of {_SUPPORTED_DTYPES}, got {self.dtype!r}",
            )

    @property
    def numpy_dtype(self) -> np.dtype:
        return np.dtype(self.dtype)


@dataclass(frozen=True)
class ServiceConfig:
    """Process-level configuration (storage / logging locations)."""

    data_dir: Path = Path("data/runtime")
    log_path: Path = Path("logs/service.jsonl")
    log_to_stderr: bool = True

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls(
            data_dir=Path(os.environ.get("SPARSE_EMBEDDINGS_DATA_DIR", "data/runtime")),
            log_path=Path(os.environ.get("SPARSE_EMBEDDINGS_LOG", "logs/service.jsonl")),
            log_to_stderr=os.environ.get("SPARSE_EMBEDDINGS_STDERR", "1") == "1",
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
