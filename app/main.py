"""FastAPI application exposing the phasing service.

Endpoints
---------
GET  /v1/health                  liveness
GET  /v1/version                 app/algorithm/dependency versions
POST /v1/phase                   phase an inline dataset
POST /v1/phase/fixture/{name}    phase a bundled synthetic fixture
GET  /v1/runs/{request_id}       provenance record for a past request
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import ALGORITHM_VERSION, __version__
from .config import REPO_ROOT, Settings, load_settings
from .errors import DatasetError, ProcessingError
from .models import PhaseRequest
from .provenance import ProvenanceStore
from .service import PhasingFailure, PhasingService

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, force=True)

    class RequestIdFilter(logging.Filter):
        """Ensure every record has a request_id for the formatter."""

        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
        ))


def create_app(settings: Settings | None = None,
               store: ProvenanceStore | None = None) -> FastAPI:
    _configure_logging()
    settings = settings or load_settings()
    store = store or ProvenanceStore(settings.provenance_db_path)
    service = PhasingService(settings, store)

    app = FastAPI(title="Allelic Haplotype Assembly", version=__version__)
    app.state.settings = settings
    app.state.store = store
    app.state.service = service

    @app.exception_handler(PhasingFailure)
    async def phasing_failure_handler(_request: Request, exc: PhasingFailure) -> JSONResponse:
        status = 422 if isinstance(exc.error, (DatasetError, ProcessingError)) else 500
        return JSONResponse(
            status_code=status,
            content={
                "request_id": exc.request_id,
                "status": "failed",
                "error": exc.error.to_dict(),
            },
        )

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/version")
    def version() -> dict:
        return {
            "app": __version__,
            "algorithm": ALGORITHM_VERSION,
            "numpy": np.__version__,
        }

    @app.post("/v1/phase", status_code=200)
    def phase_endpoint(request: PhaseRequest) -> dict:
        return service.run_phase(request)

    @app.post("/v1/phase/fixture/{name}", status_code=200)
    def phase_fixture(name: str) -> dict:
        fixtures_dir = Path(settings.fixtures_dir)
        if not fixtures_dir.is_absolute():
            fixtures_dir = REPO_ROOT / fixtures_dir
        path = (fixtures_dir / f"{name}.json").resolve()
        if not path.is_relative_to(fixtures_dir.resolve()) or not path.is_file():
            raise HTTPException(status_code=404, detail=f"unknown fixture {name!r}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return service.run_phase(PhaseRequest(**payload))

    @app.get("/v1/runs/{request_id}")
    def get_run(request_id: str) -> dict:
        record = store.get_run(request_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no run recorded for {request_id!r}")
        return record

    return app


app = create_app()
