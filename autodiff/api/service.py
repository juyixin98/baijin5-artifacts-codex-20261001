"""Verification service: runs a fixture through the core and verifies it.

This is the seam between HTTP/CLI transports and the autodiff core; it owns
the per-request Diagnostics object and translates results into plain dicts
that both transports serialise.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import numpy as np

from ..config import get_config
from ..diagnostics import REJECTED, Diagnostics
from ..fixtures import SCENARIOS, get_scenario
from ..graph import StaleGraphError, backward
from ..numeric import check_gradients
from ..tensor import Tensor


def _new_request_id(supplied: Optional[str]) -> str:
    rid = supplied or f"req-{uuid.uuid4().hex[:12]}"
    return rid


def run_gradcheck(
    scenario_name: str,
    *,
    eps: Optional[float] = None,
    atol: Optional[float] = None,
    rtol: Optional[float] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a fixture graph, backprop it, and verify against independent FD."""
    rid = _new_request_id(request_id)
    diag = Diagnostics(request_id=rid, config=get_config())

    if scenario_name not in SCENARIOS:
        diag.rejected(
            "service.unknown_scenario",
            f"scenario {scenario_name!r} is not registered",
            available=sorted(SCENARIOS),
        )
        return _report(rid, scenario_name, REJECTED, diag,
                       eps=eps, atol=atol, rtol=rtol)

    scenario = get_scenario(scenario_name)
    diag.accepted("service.scenario_loaded",
                  f"fixture {scenario_name!r} built",
                  description=scenario.description)

    built, reference = scenario.run()
    try:
        backward(built.loss)
    except StaleGraphError as exc:
        # Defensive: fixtures never mutate, but categorise it if it happens.
        diag.rejected("service.backward_stale", str(exc))
        return _report(rid, scenario_name, REJECTED, diag,
                       eps=eps, atol=atol, rtol=rtol)

    analytic = {name: _grad_or_reject(name, t, diag)
                for name, t in built.leaves.items()}

    cfg = get_config()
    result = check_gradients(
        analytic,
        lambda d: reference(d),
        built.base_inputs,
        eps=cfg.fd_eps if eps is None else eps,
        atol=cfg.fd_atol if atol is None else atol,
        rtol=cfg.fd_rtol if rtol is None else rtol,
        diagnostics=diag,
        config=cfg,
    )
    return {
        "request_id": rid,
        "scenario": scenario_name,
        "status": result.status,
        "eps": result.eps,
        "atol": result.atol,
        "rtol": result.rtol,
        "leaves": [
            {
                "name": leaf.name,
                "status": leaf.status,
                "category": leaf.category,
                "max_abs_err": leaf.max_abs_err,
                "max_rel_err": leaf.max_rel_err,
                "shape": list(leaf.shape),
                "worst_index": list(leaf.worst_index) if leaf.worst_index else None,
                "analytic_preview": leaf.analytic_preview,
                "numeric_preview": leaf.numeric_preview,
            }
            for leaf in result.leaves
        ],
        "diagnostics": [r.to_dict() for r in diag.records],
    }


def _grad_or_reject(name: str, t: Tensor, diag: Diagnostics) -> np.ndarray:
    if t.grad is None:
        # A leaf the graph never reached: do NOT substitute zero; report it.
        diag.unable(
            "service.missing_gradient",
            f"leaf {name!r} received no gradient (grad=None); cannot verify",
            leaf=name, shape=list(t.shape),
        )
        return np.full(t.shape, np.nan)
    return np.asarray(t.grad, dtype=np.float64)


def run_inplace_check(*, mutate: bool = True,
                      request_id: Optional[str] = None) -> dict[str, Any]:
    """Exercise the version-detection guard (acceptance rule 2)."""
    rid = _new_request_id(request_id)
    diag = Diagnostics(request_id=rid, config=get_config())
    x = Tensor(np.array([1.0, 2.0, 3.0]), requires_grad=True)
    y = Tensor(np.array([0.5, -1.0, 2.0]), requires_grad=True)
    loss = (x * x + y).sum()
    diag.accepted("inplace.graph_built",
                  "forward graph captured x and y",
                  x_version=x.version, y_version=y.version)

    if mutate:
        x.set_data(np.array([9.0, 8.0, 7.0]))
        diag.rejected("inplace.mutation_detected",
                      "x.set_data() bumped version after graph capture",
                      x_version=x.version)
        try:
            backward(loss)
        except StaleGraphError as exc:
            diag.rejected("inplace.backward_refused", str(exc),
                          failure_category="stale_graph_version_mismatch")
            return {
                "request_id": rid,
                "accepted": False,
                "failure_category": "stale_graph_version_mismatch",
                "message": "backward correctly refused to differentiate stale data",
                "diagnostics": [r.to_dict() for r in diag.records],
            }
        diag.accepted("inplace.backward_ran",
                      "backward ran despite mutation (unexpected)")
        return {
            "request_id": rid,
            "accepted": False,
            "failure_category": "version_guard_failed",
            "message": "backward ran on stale data; version guard is broken",
            "diagnostics": [r.to_dict() for r in diag.records],
        }

    backward(loss)
    diag.accepted("inplace.backward_ok",
                  "no mutation occurred; backward accepted",
                  x_grad=x.grad,
                  y_grad=y.grad)
    return {
        "request_id": rid,
        "accepted": True,
        "failure_category": None,
        "message": "backward accepted because no captured tensor changed",
        "diagnostics": [r.to_dict() for r in diag.records],
    }


def _report(rid, scenario_name, status, diag, *, eps, atol, rtol) -> dict[str, Any]:
    cfg = get_config()
    return {
        "request_id": rid,
        "scenario": scenario_name,
        "status": status,
        "eps": cfg.fd_eps if eps is None else eps,
        "atol": cfg.fd_atol if atol is None else atol,
        "rtol": cfg.fd_rtol if rtol is None else rtol,
        "leaves": [],
        "diagnostics": [r.to_dict() for r in diag.records],
    }
