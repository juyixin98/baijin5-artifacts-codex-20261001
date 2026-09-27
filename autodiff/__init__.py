"""mini-autodiff: a small tensor autodiff backend.

Modules
-------
config      : immutable runtime configuration and ``no_grad``
tensor      : Tensor data type, version detection, grad-state distinction
ops         : differentiable ops and broadcasting VJPs
graph       : topological backward, stale-graph rejection, release/retain
training    : parameter bundle, SGD, train/eval state
numeric     : independent finite-difference gradient verification
diagnostics : request/record-scoped structured logging with redaction
"""
from .config import Config, get_config, set_config, no_grad
from .tensor import Tensor, TensorError, tensor, zeros, ones
from . import ops
from .graph import (
    backward,
    AutodiffError,
    StaleGraphError,
    GraphReleasedError,
    NonScalarLossError,
)
from .ops import (
    add,
    sub,
    mul,
    div,
    neg,
    exp,
    log,
    relu,
    sigmoid,
    tanh,
    matmul,
    sum_,
    mean,
)
from .training import Mode, TrainingState, ParameterBundle, SGD, SGDConfig
from .numeric import (
    check_gradients,
    GradCheckResult,
    LeafResult,
    GRAD_VALUE_MISMATCH,
    GRAD_SHAPE_MISMATCH,
    NONFINITE_ANALYTIC,
    NONFINITE_REFERENCE,
)
from .diagnostics import Diagnostics, ACCEPTED, REJECTED, UNABLE

__all__ = [
    "Config", "get_config", "set_config", "no_grad",
    "Tensor", "TensorError", "tensor", "zeros", "ones",
    "ops",
    "backward",
    "AutodiffError", "StaleGraphError", "GraphReleasedError", "NonScalarLossError",
    "add", "sub", "mul", "div", "neg",
    "exp", "log", "relu", "sigmoid", "tanh",
    "matmul", "sum_", "mean",
    "Mode", "TrainingState", "ParameterBundle", "SGD", "SGDConfig",
    "check_gradients", "GradCheckResult", "LeafResult",
    "GRAD_VALUE_MISMATCH", "GRAD_SHAPE_MISMATCH",
    "NONFINITE_ANALYTIC", "NONFINITE_REFERENCE",
    "Diagnostics", "ACCEPTED", "REJECTED", "UNABLE",
]
