"""HTTP interface (FastAPI).

Error contract: domain errors carry one of four categories and map to
distinct HTTP statuses so clients can distinguish failure classes:

- input_validation   -> 422
- state_conflict     -> 409
- resource_exhausted -> 413
- computation_failure-> 500

Per-pair saturation / undefined outcomes are NOT errors: they are
reported in the pair result's `status` field with a 200 response.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .errors import AppError, ErrorCategory, ComputationFailureError
from .provenance import RunStore
from .sequences import MAX_SEQUENCES
from .service import compute_distances

_HTTP_STATUS = {
    ErrorCategory.INPUT_VALIDATION: 422,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 413,
    ErrorCategory.COMPUTATION_FAILURE: 500,
}

DEFAULT_DB_PATH = os.environ.get("DISTANCE_DB_PATH", "distance_runs.sqlite3")


class SequenceInput(BaseModel):
    id: str
    sequence: str


class BootstrapSpec(BaseModel):
    # Upper limits are enforced by the domain layer so that exceeding them
    # surfaces as resource_exhausted (413), not a schema 422.
    replicates: int = Field(default=1000, ge=1)
    confidence: float = Field(default=0.95, gt=0.0, lt=1.0)
    seed: int = Field(default=0, ge=0)


class DistanceRequest(BaseModel):
    sequences: list[SequenceInput] | None = Field(default=None, max_length=MAX_SEQUENCES)
    fasta: str | None = None
    model: Literal["p", "jc69", "k2p"]
    bootstrap: BootstrapSpec | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("run_id")
    @classmethod
    def _run_id_charset(cls, v: str | None) -> str | None:
        if v is not None and not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("run_id may only contain alphanumerics, '-' and '_'")
        return v

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def create_app(db_path: str | None = None) -> FastAPI:
    store = RunStore(db_path or DEFAULT_DB_PATH)
    app = FastAPI(title="synthetic-distance-service", version="0.1.0")
    app.state.store = store

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=_HTTP_STATUS[exc.category],
                            content={"error": exc.to_dict()})

    @app.exception_handler(Exception)
    async def _unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        wrapped = ComputationFailureError("unexpected_failure", f"{type(exc).__name__}: {exc}")
        return JSONResponse(status_code=500, content={"error": wrapped.to_dict()})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/distances", status_code=200)
    def post_distances(req: DistanceRequest) -> dict[str, Any]:
        return compute_distances(req.to_payload(), store)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        stored = store.get_run(run_id)
        if stored is None:
            return JSONResponse(
                status_code=404,
                content={"error": {
                    "category": ErrorCategory.INPUT_VALIDATION.value,
                    "code": "run_not_found",
                    "message": f"no run recorded with id {run_id!r}",
                    "details": {"run_id": run_id},
                }},
            )
        return {
            "run_id": stored.run_id,
            "model": stored.model,
            "seed": stored.seed,
            "status": stored.status,
            "request": json.loads(stored.request_json),
            "pair_results": list(stored.pair_results),
        }

    return app


app = create_app()
