"""FastAPI application exposing gradient verification endpoints."""
from __future__ import annotations

from fastapi import FastAPI

from ..fixtures import SCENARIOS
from .schemas import (
    GradCheckRequest,
    GradCheckResponse,
    HealthResponse,
    InplaceCheckRequest,
    InplaceCheckResponse,
)
from .service import run_gradcheck, run_inplace_check

app = FastAPI(
    title="mini-autodiff verification API",
    version="1.0.0",
    description="Synthetic-fixture gradient checks for the small autodiff backend.",
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="mini-autodiff",
        scenarios=sorted(SCENARIOS),
    )


@app.post("/verify/gradients", response_model=GradCheckResponse)
def verify_gradients(req: GradCheckRequest) -> GradCheckResponse:
    report = run_gradcheck(
        req.scenario,
        eps=req.eps, atol=req.atol, rtol=req.rtol,
        request_id=req.request_id,
    )
    return GradCheckResponse(**report)


@app.post("/verify/inplace", response_model=InplaceCheckResponse)
def verify_inplace(req: InplaceCheckRequest) -> InplaceCheckResponse:
    report = run_inplace_check(mutate=req.mutate, request_id=req.request_id)
    return InplaceCheckResponse(**report)


@app.get("/scenarios")
def scenarios() -> dict[str, list[str]]:
    return {"scenarios": sorted(SCENARIOS)}
