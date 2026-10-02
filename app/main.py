"""Application factory.

Wires the configuration layer, structured logging, the job manager and the
API router together. ``create_app`` is the single entry point used by both
uvicorn (``app.main:app``) and the test-suite.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import create_router
from app.config import Settings, collect_versions
from app.jobs.manager import JobManager
from app.logging_setup import configure_logging, get_logger, log_event


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    logger = get_logger()
    versions = collect_versions().as_dict()
    job_manager = JobManager(settings)

    app = FastAPI(title="marker-watershed-backend", version=versions["app"])
    app.state.settings = settings
    app.state.job_manager = job_manager
    app.state.versions = versions

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Unknown states are reported as errors, never as success.
        log_event(
            logger, 40, "unhandled_exception",
            path=request.url.path, error_type=type(exc).__name__, error_message=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={"error": {"category": "internal_error", "message": f"{type(exc).__name__}: {exc}"}},
        )

    app.include_router(create_router(settings, job_manager, versions))
    log_event(logger, 20, "app_started", versions=versions, settings=settings.__dict__)
    return app


app = create_app()
