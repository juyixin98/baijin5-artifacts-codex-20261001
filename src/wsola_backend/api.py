"""FastAPI surface for the WSOLA stretch backend.

Audio travels as base64-encoded little-endian float32 mono PCM. Responses
carry the per-segment match offsets alongside the stretched audio and the
diagnostic verdict. Contract violations map to HTTP 422 with a
machine-readable failure_category.
"""
from __future__ import annotations

import base64

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .contracts import ContractViolation, Decision, FailureCategory
from .diagnostics import new_request_id
from .service import run_stretch

app = FastAPI(title="wsola-backend", version="0.1.0")


class StretchRequestModel(BaseModel):
    sample_rate: int
    time_scale: float
    samples_b64: str = Field(description="base64 of little-endian float32 mono PCM")
    input_label: str | None = Field(default=None, description="caller label; logged only as a hash")


class StretchResponseModel(BaseModel):
    request_id: str
    decision: Decision
    notes: list[str]
    target_length: int
    n_frames: int
    offsets: list[int]
    match_positions: list[int]
    samples_b64: str


def _decode_samples(samples_b64: str) -> np.ndarray:
    raw = base64.b64decode(samples_b64, validate=True)
    if len(raw) % 4 != 0:
        raise ValueError("payload length is not a multiple of 4 bytes (float32)")
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


@app.exception_handler(ContractViolation)
async def contract_violation_handler(request: Request, exc: ContractViolation) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "request_id": getattr(request.state, "request_id", None),
            "decision": Decision.REJECTED.value,
            "failure_category": exc.category.value,
            "detail": exc.message,
        },
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or new_request_id()
    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id
    return response


@app.post("/v1/stretch", response_model=StretchResponseModel)
def stretch(body: StretchRequestModel, request: Request) -> StretchResponseModel:
    request_id: str = request.state.request_id
    try:
        samples = _decode_samples(body.samples_b64)
    except (ValueError, TypeError) as exc:
        raise ContractViolation(FailureCategory.MALFORMED_PAYLOAD, f"cannot decode samples_b64: {exc}") from exc
    outcome = run_stretch(
        samples,
        body.sample_rate,
        body.time_scale,
        request_id=request_id,
        input_label=body.input_label,
    )
    result = outcome.result
    return StretchResponseModel(
        request_id=request_id,
        decision=outcome.decision,
        notes=outcome.notes,
        target_length=result.target_length,
        n_frames=len(result.frames),
        offsets=result.offsets,
        match_positions=[f.match_pos for f in result.frames],
        samples_b64=base64.b64encode(result.output.astype("<f4").tobytes()).decode("ascii"),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
