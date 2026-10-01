"""HTTP API wiring.

Endpoints
---------
POST /api/v1/pvalue     - exact or Monte-Carlo paired randomization p-value
POST /api/v1/inversion  - (1-alpha) constant-effect confidence set
POST /api/v1/replay     - deterministic replay of a Monte-Carlo run
GET  /api/v1/requests/{request_id} - persisted request + results
GET  /api/v1/fixtures   - catalogue of synthetic fixtures and reference values
GET  /health            - liveness + version
GET  /                   - service contract summary

Every response/error carries the correlated ``request_id`` and the service
version. Failures are categorized by stable machine-readable codes.
"""
from __future__ import annotations

import json as json_lib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__, service
from app.config import settings
from app.core.contract import ContractError
from app.diagnostics import bind_request_id
from app.repro.fixtures import FIXTURES, as_pairs
from app.storage import get_database

app = FastAPI(
    title="Paired Randomized Experiment Inference",
    version=__version__,
    description=(
        "Exact randomization tests and constant-effect confidence-set "
        "inversion for paired randomized experiments."
    ),
)


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id")
    return supplied if supplied else service.new_request_id()


def _error_body(request_id: str, code: str, message: str,
                details: dict | None = None) -> dict:
    return {
        "request_id": request_id,
        "version": __version__,
        "status": "error",
        "failure": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


@app.exception_handler(ContractError)
async def contract_error_handler(request: Request, exc: ContractError):
    request_id = getattr(request.state, "request_id", None) or _request_id(
        request
    )
    return JSONResponse(
        status_code=422,
        content=_error_body(request_id, exc.code, exc.message, exc.details),
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(json_lib.JSONDecodeError)
async def malformed_json_handler(request: Request,
                                 exc: json_lib.JSONDecodeError):
    request_id = _request_id(request)
    return JSONResponse(
        status_code=422,
        content=_error_body(
            request_id, "MALFORMED_DATA", "request body is not valid JSON",
            {"detail": exc.msg},
        ),
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or _request_id(
        request
    )
    return JSONResponse(
        status_code=500,
        content=_error_body(
            request_id,
            "INTERNAL_ERROR",
            f"unexpected failure: {type(exc).__name__}",
        ),
        headers={"X-Request-ID": request_id},
    )


@app.middleware("http")
async def correlate_requests(request: Request, call_next):
    request_id = _request_id(request)
    request.state.request_id = request_id
    bind_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Service-Version"] = __version__
    return response


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": __version__,
    }


@app.get("/")
async def contract_overview():
    from app.core import contract

    return {
        "service": settings.service_name,
        "version": __version__,
        "design": "paired randomized experiment",
        "randomization": "one independent fair coin per pair; 2**n assignments",
        "statistic": contract.TWO_SIDED_DEFINITION,
        "exact_budget_flips": settings.exact_budget_flips,
        "endpoints": [
            "POST /api/v1/pvalue",
            "POST /api/v1/inversion",
            "POST /api/v1/replay",
            "GET  /api/v1/requests/{request_id}",
            "GET  /api/v1/fixtures",
        ],
    }


@app.post("/api/v1/pvalue")
async def pvalue(request: Request):
    payload = await request.json()
    return service.analyze_pvalue(payload, request.state.request_id)


@app.post("/api/v1/inversion")
async def inversion(request: Request):
    payload = await request.json()
    return service.analyze_inversion(payload, request.state.request_id)


@app.post("/api/v1/replay")
async def replay(request: Request):
    payload = await request.json()
    return service.analyze_replay(payload, request.state.request_id)


@app.get("/api/v1/requests/{request_id}")
async def get_request(request_id: str):
    record = get_database().get_request(request_id)
    if record is None:
        return JSONResponse(
            status_code=404,
            content=_error_body(request_id, "UNKNOWN_REQUEST",
                                "no stored request with this id"),
        )
    return {"request_id": request_id, "version": __version__, **record}


@app.get("/api/v1/fixtures")
async def list_fixtures():
    return {
        "version": __version__,
        "fixtures": [
            {
                "name": fx.name,
                "description": fx.description,
                "differences": list(fx.differences),
                "example_pairs": [list(p) for p in as_pairs(fx)],
                "reference": fx.reference,
            }
            for fx in FIXTURES.values()
        ],
    }
