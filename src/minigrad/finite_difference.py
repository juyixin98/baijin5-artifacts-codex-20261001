"""Numerical validation: central finite differences vs. minigrad gradients.

The reference gradients are computed by perturbing a *pure NumPy* loss
function (see validation_cases.py) — never by the autodiff engine under
test — so the check is genuinely independent of the backward implementation.

Verdicts:
* ``accepted``    — max error ratio <= 1 (within atol + rtol * |numerical|)
* ``undecidable`` — ratio in (1, undecidable_factor], or non-finite values
                    made the comparison unreliable
* ``rejected``    — ratio > undecidable_factor, or a gradient is missing
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Settings, get_settings
from .diagnostics import (
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_UNDECIDABLE,
)
from .tensor import Tensor

_STATUS_RANK = {STATUS_ACCEPTED: 0, STATUS_UNDECIDABLE: 1, STATUS_REJECTED: 2}


def central_difference_grad(f, x: np.ndarray, eps: float) -> np.ndarray:
    """Central-difference gradient of scalar ``f`` at ``x``.

    For an empty ``x`` (a shape containing 0) there is nothing to perturb;
    the result is an empty gradient of the same shape.
    """
    x = np.asarray(x, dtype=np.float64)
    grad = np.zeros_like(x)
    for index in np.ndindex(x.shape):
        perturbed_up = x.copy()
        perturbed_up[index] += eps
        perturbed_down = x.copy()
        perturbed_down[index] -= eps
        grad[index] = (f(perturbed_up) - f(perturbed_down)) / (2.0 * eps)
    return grad


@dataclass
class ParamReport:
    name: str
    shape: tuple
    max_error_ratio: float
    status: str
    reason: str


@dataclass
class GradcheckReport:
    case: str
    status: str
    reason: str
    tolerance: dict
    params: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case": self.case,
            "status": self.status,
            "reason": self.reason,
            "tolerance": self.tolerance,
            "params": [
                {
                    "name": p.name,
                    "shape": list(p.shape),
                    "max_error_ratio": p.max_error_ratio,
                    "status": p.status,
                    "reason": p.reason,
                }
                for p in self.params
            ],
        }


def _check_param(name, analytic, numeric, settings: Settings) -> ParamReport:
    shape = np.shape(numeric)
    if analytic is None:
        return ParamReport(
            name, shape, float("inf"), STATUS_REJECTED,
            "autodiff produced no gradient (grad is None) but the parameter "
            "feeds the scalar loss",
        )
    analytic = np.asarray(analytic, dtype=np.float64)
    if analytic.shape != shape:
        return ParamReport(
            name, shape, float("inf"), STATUS_REJECTED,
            f"gradient shape {analytic.shape} != parameter shape {shape}",
        )
    if not (np.all(np.isfinite(analytic)) and np.all(np.isfinite(numeric))):
        return ParamReport(
            name, shape, float("nan"), STATUS_UNDECIDABLE,
            "non-finite values in analytic or numerical gradient; "
            "cannot judge agreement",
        )
    error = np.abs(analytic - numeric)
    tolerance = settings.fd_atol + settings.fd_rtol * np.abs(numeric)
    if error.size == 0:
        ratio = 0.0  # empty parameter: vacuously in agreement
    else:
        ratio = float(np.max(error / tolerance))
    if ratio <= 1.0:
        return ParamReport(name, shape, ratio, STATUS_ACCEPTED,
                           "within finite-difference tolerance")
    if ratio <= settings.undecidable_factor:
        return ParamReport(
            name, shape, ratio, STATUS_UNDECIDABLE,
            f"error is {ratio:.3g}x tolerance: above acceptance but below "
            "the rejection threshold (possible finite-difference noise)",
        )
    return ParamReport(
        name, shape, ratio, STATUS_REJECTED,
        f"error is {ratio:.3g}x tolerance: analytic and numerical gradients "
        "disagree beyond finite-difference noise",
    )


def gradcheck(case, settings: Settings | None = None) -> GradcheckReport:
    """Compare minigrad gradients against central finite differences."""
    settings = settings or get_settings()

    tensors = {
        name: Tensor(value.copy(), requires_grad=True, name=name)
        for name, value in case.params.items()
    }
    loss = case.tensor_fn(tensors)
    loss.backward()

    params = []
    for name, value in case.params.items():
        numerical = central_difference_grad(
            lambda perturbed, _n=name: case.numpy_fn({**case.params, _n: perturbed}),
            np.asarray(value, dtype=np.float64),
            settings.fd_epsilon,
        )
        params.append(_check_param(name, tensors[name].grad, numerical, settings))

    worst = max(params, key=lambda p: _STATUS_RANK[p.status])
    return GradcheckReport(
        case=case.name,
        status=worst.status,
        reason=worst.reason,
        tolerance={
            "eps": settings.fd_epsilon,
            "atol": settings.fd_atol,
            "rtol": settings.fd_rtol,
            "undecidable_factor": settings.undecidable_factor,
        },
        params=params,
    )
