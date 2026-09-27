"""FastAPI service for minigrad.

Endpoints
---------
* ``GET  /health``                  — liveness
* ``POST /v1/graph/execute``        — build a graph from a program spec, run
                                      forward + backward, return gradients
* ``POST /v1/gradcheck/{case}``     — finite-difference validation of a
                                      registered case
* ``GET  /v1/gradcheck``            — list registered cases

Every response carries a ``request_id`` (also in the ``x-request-id``
header). Every accept/reject/undecidable decision is emitted as a structured
``Diagnostic`` — in the response body and in the JSON log — with masked
tensor state (shapes/versions only, never values).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ops
from .diagnostics import (
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    Diagnostic,
    log_diagnostic,
    tensor_state,
)
from .errors import (
    BackwardError,
    GraphFreedError,
    InplaceModificationError,
    UnknownCaseError,
)
from .finite_difference import gradcheck
from .tensor import Tensor
from .validation_cases import get_case, list_cases

app = FastAPI(title="minigrad", version="0.1.0")


# --------------------------------------------------------------------------
# request id middleware
# --------------------------------------------------------------------------

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

class TensorSpec(BaseModel):
    data: Any  # nested lists / scalars, converted to float64
    requires_grad: bool = False


class Step(BaseModel):
    op: str
    out: str | None = None
    args: list = Field(default_factory=list)
    kwargs: dict = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    inputs: dict[str, TensorSpec]
    program: list[Step]
    loss: str
    gradients: list[str]
    retain_graph: bool = False


# --------------------------------------------------------------------------
# program interpreter
# --------------------------------------------------------------------------

_OPS = {
    "add": ops.add,
    "sub": ops.sub,
    "mul": ops.mul,
    "div": ops.div,
    "pow": ops.pow,
    "neg": ops.neg,
    "matmul": ops.matmul,
    "sum": ops.sum,
    "mean": ops.mean,
    "max": ops.max,
    "exp": ops.exp,
    "log": ops.log,
    "sigmoid": ops.sigmoid,
    "tanh": ops.tanh,
    "relu": ops.relu,
    "reshape": ops.reshape,
    "transpose": ops.transpose,
    "broadcast_to": ops.broadcast_to,
}


def _resolve(arg, env):
    if isinstance(arg, str):
        if arg not in env:
            raise KeyError(f"name {arg!r} is not defined in the program")
        return env[arg]
    if isinstance(arg, (int, float)):
        return arg
    raise ValueError(f"unsupported argument {arg!r}: use a name or a number")


def _apply_step(step: Step, env: dict) -> None:
    if step.op == "setitem":
        # in-place write through the tracked Tensor API; bumps the version
        # counter so a later backward through a dependent graph is rejected
        target = _resolve(step.args[0], env)
        index = tuple(step.kwargs["index"])
        target[index] = step.kwargs["value"]
        return
    fn = _OPS.get(step.op)
    if fn is None:
        raise ValueError(f"unknown op {step.op!r}")
    if not step.out:
        raise ValueError(f"op {step.op!r} requires an 'out' name")
    args = [_resolve(a, env) for a in step.args]
    env[step.out] = fn(*args, **step.kwargs)


def _diagnostic_response(request: Request, status_code: int, diagnostic: Diagnostic,
                         extra: dict | None = None) -> JSONResponse:
    log_diagnostic(diagnostic)
    body = {
        "request_id": diagnostic.request_id,
        "diagnostics": [diagnostic.to_dict()],
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/graph/execute")
def execute(request_body: ExecuteRequest, request: Request):
    request_id = request.state.request_id
    env: dict[str, Tensor] = {}
    for name, spec in request_body.inputs.items():
        env[name] = Tensor(spec.data, requires_grad=spec.requires_grad, name=name)

    try:
        for step in request_body.program:
            _apply_step(step, env)
        if request_body.loss not in env:
            raise KeyError(f"loss name {request_body.loss!r} is not defined")
        loss = env[request_body.loss]
        loss.backward(retain_graph=request_body.retain_graph)
        gradients = {}
        for name in request_body.gradients:
            if name not in env:
                raise KeyError(f"gradient target {name!r} is not defined")
            tensor = env[name]
            # None (no gradient flowed) stays null; zeros stay zeros
            gradients[name] = None if tensor.grad is None else tensor.grad.tolist()
    except InplaceModificationError as exc:
        return _diagnostic_response(
            request, 409,
            Diagnostic(
                request_id=request_id,
                component="backward",
                status=STATUS_REJECTED,
                reason=str(exc),
                state={"op": exc.op_name, "tensor": exc.tensor_name,
                       "expected_version": exc.expected_version,
                       "actual_version": exc.actual_version},
            ),
        )
    except GraphFreedError as exc:
        return _diagnostic_response(
            request, 409,
            Diagnostic(request_id=request_id, component="backward",
                       status=STATUS_REJECTED, reason=str(exc),
                       state={"op": exc.op_name}),
        )
    except BackwardError as exc:
        return _diagnostic_response(
            request, 422,
            Diagnostic(request_id=request_id, component="backward",
                       status=STATUS_REJECTED, reason=str(exc)),
        )
    except (KeyError, ValueError) as exc:
        return _diagnostic_response(
            request, 422,
            Diagnostic(request_id=request_id, component="program",
                       status=STATUS_REJECTED, reason=str(exc)),
        )

    diagnostic = Diagnostic(
        request_id=request_id,
        component="execute",
        status=STATUS_ACCEPTED,
        reason="forward and backward completed",
        state={
            "loss": request_body.loss,
            "tensors": {name: tensor_state(t) for name, t in env.items()},
        },
    )
    log_diagnostic(diagnostic)
    return {
        "request_id": request_id,
        "loss": float(loss.data),
        "gradients": gradients,
        "diagnostics": [diagnostic.to_dict()],
    }


@app.get("/v1/gradcheck")
def gradcheck_cases():
    return {"cases": list_cases()}


@app.post("/v1/gradcheck/{case_name}")
def gradcheck_run(case_name: str, request: Request):
    request_id = request.state.request_id
    try:
        case = get_case(case_name)
    except UnknownCaseError as exc:
        return _diagnostic_response(
            request, 404,
            Diagnostic(request_id=request_id, component="gradcheck",
                       status=STATUS_REJECTED, reason=str(exc),
                       state={"known_cases": list_cases()}),
        )
    report = gradcheck(case)
    diagnostic = Diagnostic(
        request_id=request_id,
        component="gradcheck",
        status=report.status,
        reason=report.reason,
        state={"case": case.name,
               "max_error_ratio": max(p.max_error_ratio for p in report.params)},
    )
    log_diagnostic(diagnostic)
    return {
        "request_id": request_id,
        "report": report.to_dict(),
        "diagnostics": [diagnostic.to_dict()],
    }
