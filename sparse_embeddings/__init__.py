"""Sparse embedding gradient accumulation and optimizer service.

Layered package layout:

* ``tensor_types``  - validated sparse tensor types and error categories
* ``graph``         - aggregation / clipping / sparse optimizer math (pure NumPy)
* ``training``      - in-memory table state, service orchestration, persistence
* ``validation``    - independent dense reference implementation and numeric checks
"""

from sparse_embeddings.config import (
    ClippingConfig,
    OptimizerConfig,
    ServiceConfig,
    TableConfig,
)
from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)

__version__ = "1.0.0"

__all__ = [
    "ClippingConfig",
    "ErrorCategory",
    "OptimizerConfig",
    "ServiceConfig",
    "SparseEmbeddingError",
    "SparseGradientBatch",
    "TableConfig",
    "__version__",
]
