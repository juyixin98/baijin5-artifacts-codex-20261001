"""TensorCraft: stride-layout tensor backend.

Modules:
    tensor      -- tensor type: shape / strides / offset over a 1-D buffer
    graph       -- computation graph, executor and execution traces
    state       -- tensor/graph registries and training state
    validation  -- independent NumPy oracle and value/alias verification
    api         -- FastAPI service exposing the core
"""

__version__ = "0.1.0"

from .tensor import (
    Layout,
    Order,
    Storage,
    Tensor,
    elementwise,
    matmul,
    reduce_sum,
    resolve_dtype,
)

__all__ = [
    "Layout", "Order", "Storage", "Tensor", "elementwise", "matmul",
    "reduce_sum", "resolve_dtype",
]
