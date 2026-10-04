"""FastAPI boundary.

Maps the closed error taxonomy onto HTTP statuses:
    INPUT_VALIDATION   -> 422
    STATE_CONFLICT     -> 409
    RESOURCE_EXHAUSTED -> 413
    COMPUTATION_FAILED -> 500
    NOT_FOUND          -> 404

The app is built by ``create_app`` so tests can point the SQLite store and
the log file at a tmp_path. Configuration is via environment variables
``NJ_DB_PATH`` and ``NJ_LOG_FILE`` with local-file defaults.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import runlog
from .errors import ErrorCategory, NJServiceError, NotFoundError
from .models import TreeRequest, TreeResponse
from .service import build_tree
from .store import RunStore

_STATUS_BY_CATEGORY = {
    ErrorCategory.INPUT_VALIDATION: 422,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 413,
    ErrorCategory.COMPUTATION_FAILED: 500,
    ErrorCategory.NOT_FOUND: 404,
}


def create_app(db_path: str | None = None, log_file: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("NJ_DB_PATH", "./nj_runs.db")
    log_file = log_file if log_file is not None else os.environ.get(
        "NJ_LOG_FILE", "./nj_service.log"
    )
    runlog.configure_logging(log_file)
    store = RunStore(db_path)

    app = FastAPI(title="nj-backend", version="0.1.0")
    app.state.store = store

    @app.exception_handler(NJServiceError)
    async def service_error_handler(
        _request: Request, exc: NJServiceError
    ) -> JSONResponse:
        status = _STATUS_BY_CATEGORY[exc.category]
        body = exc.to_dict()
        body["run_id"] = getattr(exc, "run_id", None)
        return JSONResponse(status_code=status, content={"error": body})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/trees", response_model=TreeResponse)
    def post_tree(request: TreeRequest) -> TreeResponse:
        return build_tree(request, store)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        record = store.get_run(run_id)
        if record is None:
            raise NotFoundError("no run with this id", {"run_id": run_id})
        return record

    return app


app = create_app()
