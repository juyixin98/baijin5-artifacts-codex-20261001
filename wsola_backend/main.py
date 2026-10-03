"""FastAPI application: audio-only WSOLA time-stretch backend."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .contracts import (
    DiagnosticRecord,
    RejectionDetail,
    TimeStretchRequest,
    TimeStretchResponse,
)
from .service import RejectionError, run_time_stretch

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="wsola-backend", version="0.1.0")


@app.exception_handler(RejectionError)
async def rejection_handler(_: Request, exc: RejectionError) -> JSONResponse:
    detail = RejectionDetail(
        request_id=exc.request_id,
        error=exc.record,
        diagnostics=[
            DiagnosticRecord(**d.as_dict()) for d in exc.log.records
        ],
    )
    return JSONResponse(status_code=422, content={"detail": detail.model_dump()})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/time-stretch", response_model=TimeStretchResponse)
def time_stretch(request: TimeStretchRequest) -> TimeStretchResponse:
    return run_time_stretch(request)
