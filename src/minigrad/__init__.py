"""minigrad: a small tensor autodiff backend.

Modules:
* ``tensor``             — the Tensor type (version counter, grad semantics)
* ``graph``              — computation graph nodes and the backward engine
* ``ops``                — differentiable operators (broadcasting, matmul,
                           reductions, activations)
* ``training``           — grad mode, Parameter, SGD
* ``finite_difference``  — numerical validation against pure-NumPy references
* ``validation_cases``   — the gradcheck case registry
* ``api``                — FastAPI service exposing execution and gradcheck
* ``diagnostics``        — structured accept/reject/undecidable records
"""

from .errors import (
    BackwardError,
    GraphFreedError,
    InplaceModificationError,
    MinigradError,
    NonScalarBackwardError,
    UnknownCaseError,
)
from .tensor import Tensor
from .training import Parameter, SGD, enable_grad, is_grad_enabled, no_grad

__all__ = [
    "Tensor",
    "Parameter",
    "SGD",
    "no_grad",
    "enable_grad",
    "is_grad_enabled",
    "MinigradError",
    "InplaceModificationError",
    "GraphFreedError",
    "BackwardError",
    "NonScalarBackwardError",
    "UnknownCaseError",
]

__version__ = "0.1.0"
