"""FastAPI application and routes."""

from __future__ import annotations

from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.schemas import (
    RegisterExperimentIn,
    RunEstimationIn,
    UploadIn,
)
from app.core.config import load_config
from app.core.errors import ErrorCode, EstimationError
from app.core.logging_setup import configure_logging, get_logger, new_run_id
from app.storage.db import Database
from app.storage.service import ExperimentService

# Error code -> HTTP status. Nothing maps an unknown/failed state to 200/201.
_STATUS: dict[ErrorCode, int] = {code: 422 for code in ErrorCode}
_STATUS[ErrorCode.DUPLICATE_UNIT] = 409
_STATUS[ErrorCode.SCHEMA_CONFLICT] = 409
_STATUS[ErrorCode.UNKNOWN_EXPERIMENT] = 404
_STATUS[ErrorCode.UNKNOWN_RUN] = 404


def create_app(db_path: str | None = None, config_path: str | None = None) -> FastAPI:
    config = load_config(config_path)
    if db_path is None:
        db_path = str(config.absolute_sqlite_path)
    run_id = configure_logging(config.absolute_log_dir, config.log_level, run_id=new_run_id("svc"))
    logger = get_logger("api", run_id)
    logger.info("FastAPI app factory | db=%s", db_path)

    service = ExperimentService(Database(db_path), config)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield
        service.db.close()

    app = FastAPI(title="CUPED backend", version="0.1.0", lifespan=_lifespan)
    app.state.service = service

    def error_response(exc: EstimationError, current_run_id: str | None = None) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS.get(exc.code, 422),
            content={
                "error_code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
                "run_id": current_run_id,
            },
        )

    @app.exception_handler(EstimationError)
    def _handle_estimation_error(request: Request, exc: EstimationError) -> JSONResponse:
        return error_response(exc, getattr(request.state, "run_id", None))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/experiments", status_code=201)
    def register_experiment(body: RegisterExperimentIn) -> dict[str, str]:
        service.register(
            body.experiment_id,
            body.description,
            [c.model_dump() for c in body.covariates],
        )
        return {"experiment_id": body.experiment_id, "status": "registered"}

    @app.post("/experiments/{experiment_id}/observations", status_code=201)
    def upload_observations(experiment_id: str, body: UploadIn) -> dict[str, Any]:
        n = service.upload(experiment_id, [o.model_dump() for o in body.observations])
        return {"experiment_id": experiment_id, "n_observations": n}

    @app.post("/experiments/{experiment_id}/runs", status_code=201)
    def create_run(experiment_id: str, body: RunEstimationIn, request: Request) -> dict[str, Any]:
        rid = new_run_id()
        request.state.run_id = rid
        try:
            return service.run_estimation(
                experiment_id,
                covariate_names=body.covariate_names,
                missing_strategy=body.missing_strategy,
                zero_variance_strategy=body.zero_variance_strategy,
                theta_source=body.theta_source,
                run_id=rid,
            )
        except EstimationError as exc:
            # Service already persisted the failed run; surface its run id.
            return JSONResponse(
                status_code=_STATUS.get(exc.code, 422),
                content={
                    "error_code": exc.code.value,
                    "message": exc.message,
                    "details": exc.details,
                    "run_id": rid,
                },
            )

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return service.get_run(run_id)

    @app.get("/experiments/{experiment_id}/runs")
    def list_runs(experiment_id: str) -> dict[str, Any]:
        service.db.require_experiment(experiment_id)
        return {"experiment_id": experiment_id, "runs": service.db.list_runs(experiment_id)}

    return app


def main() -> None:
    import uvicorn

    app = create_app()
    cfg = app.state.service.config
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
