"""FastAPI application: typed HTTP surface over the statistical kernel."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core.contracts import ErrorCode, EstimationError, Settings
from ..core.service import run_analysis
from .logging_config import configure_logging, get_logger
from .schemas import AnalysisRequest
from .storage import RunStore


def create_app(
    settings: Settings | None = None,
    store: RunStore | None = None,
    config_path: str | Path = "config/default.json",
) -> FastAPI:
    app = FastAPI(
        title="CUPED / Linear Covariate Adjustment Backend",
        version="0.1.0",
        description=(
            "Continuous-outcome randomized experiments: unadjusted group "
            "comparison, CUPED and Lin (2013) covariate-adjusted regression, "
            "reported side by side with diagnostics."
        ),
    )

    file_settings = _load_file_settings(config_path)
    app.state.settings = settings or file_settings
    log_cfg = _load_log_config(config_path)
    configure_logging(level=log_cfg.get("level", "INFO"),
                      log_dir=log_cfg.get("dir"))
    logger = get_logger()
    app.state.store = store or RunStore(_load_db_path(config_path))

    # ------------------------------------------------------------------ #
    # error handlers — domain failures keep their category, never collapse
    # ------------------------------------------------------------------ #
    @app.exception_handler(EstimationError)
    async def _domain_error_handler(_: Request, exc: EstimationError) -> JSONResponse:
        status = {
            ErrorCode.RUN_NOT_FOUND: 404,
            ErrorCode.LEAKAGE_DETECTED: 422,
        }.get(exc.code, 400)
        logger.warning("analysis rejected", extra={
            "error_code": exc.code.value, "details": exc.details,
        })
        return JSONResponse(status_code=status,
                            content={"error": exc.to_dict()})

    @app.exception_handler(Exception)
    async def _unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Unknown states are reported as failures, never as success.
        logger.exception("unexpected internal error")
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": f"internal error: {type(exc).__name__}: {exc}",
                "details": {},
            }},
        )

    # ------------------------------------------------------------------ #
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "cuped-backend", "version": "0.1.0"}

    @app.post("/api/v1/runs", status_code=201)
    def create_run(req: AnalysisRequest) -> dict:
        payload = req.model_dump(mode="json")
        run_settings = app.state.settings
        # Per-request overrides from request fields.
        overrides = {
            "theta_source": payload["theta_source"],
            "se_type": payload["se_type"],
            "regression_interactions": payload["regression_interactions"],
            "missing_policy": payload["missing_policy"],
            "zero_variance_policy": payload["zero_variance_policy"],
            "leakage_policy": payload["leakage_policy"],
            "smd_threshold": payload["smd_threshold"],
            "alpha": payload["alpha"],
            "ci_level": payload["ci_level"],
        }
        run_settings = Settings.from_dict({**run_settings.to_dict(), **overrides})

        logger.info("analysis started", extra={"step": "prepare", "progress": 1})
        result = run_analysis(payload, run_settings)
        logger.info(
            "analysis finished",
            extra={
                "run_id": result.run_id,
                "data_fingerprint": result.data_fingerprint,
                "step": "done",
                "progress": 5,
                "versions": result.versions,
            },
        )
        app.state.store.save(result)
        return result.to_dict()

    @app.get("/api/v1/runs")
    def list_runs(limit: int = 50) -> dict:
        limit = max(1, min(limit, 200))
        return {"runs": app.state.store.list_runs(limit=limit)}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return app.state.store.get(run_id)

    return app


def _load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _load_file_settings(path: str | Path) -> Settings:
    raw = _load_json(path)
    return Settings.from_dict(raw.get("defaults", {}))


def _load_db_path(path: str | Path) -> str:
    return _load_json(path).get("database", {}).get("path", "data/experiments.db")


def _load_log_config(path: str | Path) -> dict:
    return _load_json(path).get("logging", {})


def __getattr__(name: str):
    """Lazily build the default app only when an ASGI server asks for it.

    Importing this module (tests, tooling) must not create ``data/*.db`` or
    log files as a side effect.
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
