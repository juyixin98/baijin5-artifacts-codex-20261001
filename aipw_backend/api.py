"""FastAPI HTTP boundary.

Error category -> HTTP status mapping (categories stay distinguishable):

    input_error          400 Bad Request
    computation_failure  422 Unprocessable Entity
    state_conflict       409 Conflict
    resource_exhausted   413 Payload Too Large

The API is a thin adapter: all statistics live in the service/kernel layers.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import load_config
from .errors import AipwError
from .repository import RunRepository
from .service import RunService

_STATUS = {
    "input_error": 400,
    "state_conflict": 409,
    "resource_exhausted": 413,
    "computation_failure": 422,
}


class RunRequest(BaseModel):
    x: list[list[float]] = Field(description="n x p feature matrix")
    a: list[int] = Field(description="binary treatment indicator, length n")
    y: list[float] = Field(description="observed outcome, length n")
    cluster: list[int] | None = Field(default=None, description="optional cluster ids")
    known_effect: float | None = Field(
        default=None, description="when supplied, coverage of this value is reported"
    )
    run_id: str | None = Field(default=None, description="caller-chosen run id")


class RunSummary(BaseModel):
    run_id: str
    status: str
    created_at: float
    updated_at: float
    error_category: str | None


def create_app(db_path: str | None = None, config_path: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("AIPW_DB_PATH", ":memory:")
    config_path = config_path or os.environ.get("AIPW_CONFIG_PATH")
    config = load_config(config_path) if config_path else _default_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.repo = RunRepository(db_path)
        app.state.service = RunService(app.state.repo, config)
        yield
        app.state.repo.close()

    app = FastAPI(
        title="AIPW estimation backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(AipwError)
    async def _aipw_error_handler(request, exc: AipwError):
        return JSONResponse(
            status_code=_STATUS.get(exc.category, 500),
            content={
                "error": exc.category,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/runs", status_code=201)
    async def create_run(req: RunRequest) -> dict[str, Any]:
        service: RunService = app.state.service
        evidence = service.run(
            np.asarray(req.x, dtype=np.float64),
            np.asarray(req.a, dtype=np.int64),
            np.asarray(req.y, dtype=np.float64),
            (
                np.asarray(req.cluster, dtype=np.int64)
                if req.cluster is not None
                else None
            ),
            known_effect=req.known_effect,
            run_id=req.run_id,
        )
        return evidence

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        row = app.state.repo.get(run_id)
        if row is None:
            return JSONResponse(
                status_code=404,
                content={"error": "state_conflict", "message": "unknown run_id"},
            )
        return row

    @app.get("/api/runs", response_model=list[RunSummary])
    async def list_runs(limit: int = 100) -> list[dict[str, Any]]:
        return app.state.repo.list_runs(limit=limit)

    return app


def _default_config():
    from .config import AipwConfig

    return AipwConfig()


app = create_app()
