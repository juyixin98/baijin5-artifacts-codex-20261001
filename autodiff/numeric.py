"""Independent numerical verification via central finite differences.

The reference answers here are **not** produced by the autodiff core under
test.  A gradient check is given:

* ``analytic_grads`` - cotangents computed by the core's reverse pass, and
* ``reference_fn``    - a *plain-NumPy* scalar function of the same inputs,
  hand-written independently of :mod:`autodiff`.

For each scalar input element we estimate

    df/dx_i ~= (f(x + eps e_i) - f(x - eps e_i)) / (2 eps)

by perturbing raw NumPy arrays.  The core's backward rules are never used to
generate expected values, so a bug shared by forward and backward code cannot
make a check pass spuriously.

Results are triaged ACCEPTED / REJECTED / UNABLE with a concrete failure
category and a redacted diagnostic record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from .config import Config, get_config
from .diagnostics import ACCEPTED, REJECTED, UNABLE, Diagnostics

# Failure categories - tests assert against these exact labels.
GRAD_VALUE_MISMATCH = "grad_value_mismatch"
GRAD_SHAPE_MISMATCH = "grad_shape_mismatch"
NONFINITE_ANALYTIC = "nonfinite_analytic_gradient"
NONFINITE_REFERENCE = "nonfinite_reference_value"

ArrayStruct = Any  # ndarray, or nested dict/list/tuple of ndarrays.
ReferenceFn = Callable[..., float]


@dataclass(frozen=True)
class LeafResult:
    """Per-leaf outcome of one gradient check."""

    name: str
    status: str
    category: Optional[str]
    max_abs_err: float
    max_rel_err: float
    shape: tuple[int, ...]
    worst_index: Optional[tuple[int, ...]]
    analytic_preview: list[float] = field(default_factory=list)
    numeric_preview: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class GradCheckResult:
    status: str  # ACCEPTED / REJECTED / UNABLE
    leaves: tuple[LeafResult, ...]
    eps: float
    atol: float
    rtol: float

    def all_accepted(self) -> bool:
        return self.status == ACCEPTED


# ---------------------------------------------------------------------------
# Minimal numpy pytree flatten / unflatten (independent of the autodiff core)
# ---------------------------------------------------------------------------


def _tree_flatten(struct: ArrayStruct, prefix: str = "x") -> list[tuple[str, np.ndarray]]:
    if isinstance(struct, np.ndarray):
        return [(prefix, struct)]
    if isinstance(struct, (int, float)):  # tolerate scalar leaves
        return [(prefix, np.asarray(struct, dtype=np.float64))]
    if isinstance(struct, dict):
        out: list[tuple[str, np.ndarray]] = []
        for k in sorted(struct, key=str):
            out.extend(_tree_flatten(struct[k], f"{prefix}.{k}"))
        return out
    if isinstance(struct, (list, tuple)):
        out = []
        for i, v in enumerate(struct):
            out.extend(_tree_flatten(v, f"{prefix}[{i}]"))
        return out
    raise TypeError(
        f"unsupported structure entry {struct!r} of type {type(struct).__name__}"
    )


def _tree_unflatten(template: ArrayStruct, values: list[np.ndarray]) -> ArrayStruct:
    """Rebuild a structure shaped like *template* from flat *values*."""
    if isinstance(template, np.ndarray):
        return values.pop(0)
    if isinstance(template, dict):
        return {k: _tree_unflatten(template[k], values)
                for k in sorted(template, key=str)}
    if isinstance(template, list):
        return [_tree_unflatten(v, values) for v in template]
    if isinstance(template, tuple):
        return tuple(_tree_unflatten(v, values) for v in template)
    raise TypeError(f"unsupported template entry of type {type(template)}")


def _relative_error(analytic: np.ndarray, numeric: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    diff = np.abs(analytic - numeric)
    scale = np.maximum(np.abs(analytic), np.abs(numeric))
    # Where both estimates are (near) zero the relative error is undefined;
    # the absolute term of the tolerance handles agreement there.
    rel = np.where(scale > 1e-12, diff / np.maximum(scale, 1e-12), 0.0)
    return diff, rel


# ---------------------------------------------------------------------------
# Public check
# ---------------------------------------------------------------------------


def check_gradients(
    analytic_grads: ArrayStruct,
    reference_fn: ReferenceFn,
    base_inputs: ArrayStruct,
    *,
    eps: Optional[float] = None,
    atol: Optional[float] = None,
    rtol: Optional[float] = None,
    diagnostics: Optional[Diagnostics] = None,
    config: Optional[Config] = None,
) -> GradCheckResult:
    """Compare analytic gradients against central finite differences.

    ``reference_fn`` is called with one argument rebuilt with the same nested
    structure as *base_inputs* (a bare ndarray, a dict, or a list/tuple) and
    must return a plain Python float.
    """
    cfg = config or get_config()
    eps = cfg.fd_eps if eps is None else eps
    atol = cfg.fd_atol if atol is None else atol
    rtol = cfg.fd_rtol if rtol is None else rtol
    diag = diagnostics or Diagnostics(request_id="gradcheck-local", config=cfg)

    if eps <= 0 or atol < 0 or rtol < 0:
        diag.unable(
            "gradcheck.configure",
            "eps must be positive and tolerances non-negative",
            eps=eps, atol=atol, rtol=rtol,
        )
        return GradCheckResult(UNABLE, (), eps, atol, rtol)

    try:
        flat_analytic = _tree_flatten(analytic_grads)
        flat_inputs = _tree_flatten(base_inputs)
    except TypeError as exc:
        diag.unable("gradcheck.structure", str(exc))
        return GradCheckResult(UNABLE, (), eps, atol, rtol)

    if [n for n, _ in flat_analytic] != [n for n, _ in flat_inputs]:
        diag.rejected(
            "gradcheck.structure",
            "analytic gradient structure does not match input structure",
            analytic_names=[n for n, _ in flat_analytic],
            input_names=[n for n, _ in flat_inputs],
        )
        return GradCheckResult(REJECTED, (), eps, atol, rtol)

    leaf_results: list[LeafResult] = []
    overall = ACCEPTED
    for leaf_index, ((name, ga), (_, x0)) in enumerate(
        zip(flat_analytic, flat_inputs)
    ):
        result = _check_one_leaf(
            name, np.asarray(ga, dtype=np.float64),
            np.asarray(x0, dtype=np.float64),
            leaf_index, reference_fn, base_inputs, flat_inputs,
            eps, atol, rtol, diag,
        )
        leaf_results.append(result)
        if result.status == REJECTED:
            overall = REJECTED
        elif result.status == UNABLE and overall != REJECTED:
            overall = UNABLE

    diag.emit(
        "gradcheck.summary",
        overall,
        {
            ACCEPTED: "all analytic gradients match finite differences within tolerance",
            REJECTED: "at least one analytic gradient disagrees with finite differences",
            UNABLE: "check could not be decided (non-finite reference or ill-posed)",
        }[overall],
        leaves=[r.name for r in leaf_results],
        eps=eps, atol=atol, rtol=rtol,
    )
    return GradCheckResult(overall, tuple(leaf_results), eps, atol, rtol)


def _evaluate_reference(reference_fn, template, flat_values):
    rebuilt = _tree_unflatten(template, list(flat_values))
    value = float(reference_fn(rebuilt))
    if not np.isfinite(value):
        raise FloatingPointError("reference returned non-finite value")
    return value


def _check_one_leaf(name, ga, x0, leaf_index, reference_fn, template,
                    flat_inputs, eps, atol, rtol, diag) -> LeafResult:
    if ga.shape != x0.shape:
        diag.rejected(
            "gradcheck.leaf_shape",
            f"leaf {name!r}: analytic gradient shape {ga.shape} != input {x0.shape}",
            leaf=name, analytic_shape=list(ga.shape), input_shape=list(x0.shape),
        )
        return LeafResult(name, REJECTED, GRAD_SHAPE_MISMATCH,
                          float("inf"), float("inf"), tuple(x0.shape), None)

    if x0.size == 0:
        # Empty dimension: there are no partial derivatives to estimate.  The
        # vacuous check is recorded explicitly and we only assert the analytic
        # cotangent carries the identical empty shape.
        status = ACCEPTED if ga.shape == x0.shape else REJECTED
        diag.emit(
            "gradcheck.empty_leaf",
            status,
            f"leaf {name!r} has zero elements; FD has nothing to perturb; "
            f"asserting identical empty shape",
            leaf=name, shape=list(x0.shape),
        )
        return LeafResult(name, status,
                          None if status == ACCEPTED else GRAD_SHAPE_MISMATCH,
                          0.0, 0.0, tuple(x0.shape), None)

    if not np.isfinite(ga).all():
        diag.rejected(
            "gradcheck.analytic_finite",
            f"leaf {name!r}: analytic gradient contains non-finite values",
            leaf=name, analytic=ga,
        )
        return LeafResult(name, REJECTED, NONFINITE_ANALYTIC,
                          float("inf"), float("inf"), tuple(x0.shape), None)

    numeric = np.zeros_like(x0, dtype=np.float64)
    base_values = [v.copy() for _, v in flat_inputs]
    for idx in np.ndindex(x0.shape):
        plus = [v.copy() for v in base_values]
        minus = [v.copy() for v in base_values]
        plus[leaf_index][idx] += eps
        minus[leaf_index][idx] -= eps
        try:
            f_plus = _evaluate_reference(reference_fn, template, plus)
            f_minus = _evaluate_reference(reference_fn, template, minus)
        except FloatingPointError:
            diag.unable(
                "gradcheck.reference_finite",
                f"leaf {name!r} index {idx}: reference value non-finite under "
                f"perturbation; cannot estimate this partial derivative",
                leaf=name, index=list(idx),
            )
            return LeafResult(name, UNABLE, NONFINITE_REFERENCE,
                              float("nan"), float("nan"), tuple(x0.shape),
                              tuple(int(i) for i in idx))
        numeric[idx] = (f_plus - f_minus) / (2.0 * eps)

    abs_err, rel_err = _relative_error(ga, numeric)
    max_abs = float(abs_err.max())
    max_rel = float(rel_err.max())
    worst = tuple(int(i) for i in np.unravel_index(np.argmax(abs_err), abs_err.shape))
    scale = max(float(np.abs(ga).max()), float(np.abs(numeric).max()), 1.0)
    passes = max_abs <= atol + rtol * scale
    status = ACCEPTED if passes else REJECTED
    diag.emit(
        "gradcheck.leaf_value",
        status,
        f"leaf {name!r}: max_abs_err={max_abs:.3e} max_rel_err={max_rel:.3e} "
        f"(atol={atol:g}, rtol={rtol:g})",
        leaf=name, max_abs_err=max_abs, max_rel_err=max_rel,
        worst_index=list(worst),
        analytic=ga, numeric=numeric,
    )
    preview = ga.size <= 8
    return LeafResult(
        name, status, None if passes else GRAD_VALUE_MISMATCH,
        max_abs, max_rel, tuple(x0.shape), worst,
        analytic_preview=ga.reshape(-1).tolist() if preview else [],
        numeric_preview=numeric.reshape(-1).tolist() if preview else [],
    )
