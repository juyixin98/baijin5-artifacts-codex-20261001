"""FastAPI application factory and HTTP wiring.

Run locally:
    uvicorn txmap.api.app:app --reload
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import diagnostics
from ..config import SETTINGS, Settings
from ..errors import MappingError
from ..parsing import parse_fixture_full
from ..service import MappingService
from ..storage import Repository
from .schemas import IntervalRequest, PointRequest

_HTTP_STATUS = {
    "transcript_not_found": 404,
    "invalid_interval": 400,
    "coordinate_out_of_range": 422,
    "intronic_position": 422,
    "region_not_mappable": 422,
    "validation_error": 500,
}


def _load_reference(repo: Repository, settings: Settings) -> None:
    chromosomes, patterns, transcripts = parse_fixture_full(settings.fixture_path)
    repo.replace_reference(chromosomes, patterns, transcripts)


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    settings = settings or SETTINGS
    diagnostics.configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo = repository or Repository(settings.db_path)
        if repository is None:
            _load_reference(repo, settings)
        app.state.repo = repo
        app.state.service = MappingService(repo)
        diagnostics.log_event(logging.INFO, "service_start", "bootstrap",
                              fixture=str(settings.fixture_path),
                              transcripts=repo.list_transcript_ids())
        try:
            yield
        finally:
            if repository is None:
                repo.close()

    app = FastAPI(
        title="txmap",
        version="1.0.0",
        description="Bidirectional transcript <-> genomic coordinate mapping",
        lifespan=lifespan,
    )

    @app.exception_handler(MappingError)
    async def mapping_error_handler(request: Request, exc: MappingError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "-")
        diagnostics.log_event(
            logging.WARNING, "mapping_rejected", request_id,
            error_code=exc.code, detail=exc.detail, key_state=exc.key_state,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=_HTTP_STATUS.get(exc.code, 422),
            content={
                "error": {
                    "code": exc.code,
                    "detail": exc.detail,
                    "key_state": diagnostics.redact(exc.key_state),
                },
                "request_id": request_id,
            },
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = (
            request.headers.get("x-request-id") or uuid.uuid4().hex
        )
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    def resolve_request_id(request: Request, body: PointRequest | IntervalRequest) -> str:
        return body.request_id or request.state.request_id

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        return {"status": "ok", "request_id": request.state.request_id}

    @app.get("/transcripts")
    async def list_transcripts() -> dict[str, object]:
        return {"transcripts": app.state.service.list_transcripts()}

    @app.get("/transcripts/{transcript_id}")
    async def get_transcript(transcript_id: str) -> dict[str, object]:
        return app.state.service.transcript_info(transcript_id)

    @app.post("/map/tx-to-genomic/point")
    async def tx_to_genomic_point(
        body: PointRequest, request: Request
    ) -> dict[str, object]:
        rid = resolve_request_id(request, body)
        request.state.request_id = rid
        out, _ = app.state.service.map_point(
            body.transcript_id, "tx_to_genomic", body.position, rid
        )
        diagnostics.log_event(logging.INFO, "mapping_accepted", rid,
                              direction="tx_to_genomic", mode="point",
                              transcript_id=body.transcript_id, position=body.position)
        return out

    @app.post("/map/genomic-to-tx/point")
    async def genomic_to_tx_point(
        body: PointRequest, request: Request
    ) -> dict[str, object]:
        rid = resolve_request_id(request, body)
        request.state.request_id = rid
        out, _ = app.state.service.map_point(
            body.transcript_id, "genomic_to_tx", body.position, rid
        )
        diagnostics.log_event(logging.INFO, "mapping_accepted", rid,
                              direction="genomic_to_tx", mode="point",
                              transcript_id=body.transcript_id, position=body.position)
        return out

    @app.post("/map/tx-to-genomic/interval")
    async def tx_to_genomic_interval(
        body: IntervalRequest, request: Request
    ) -> dict[str, object]:
        rid = resolve_request_id(request, body)
        request.state.request_id = rid
        out, _ = app.state.service.map_interval(
            body.transcript_id, "tx_to_genomic", body.start, body.end, rid
        )
        diagnostics.log_event(logging.INFO, "mapping_accepted", rid,
                              direction="tx_to_genomic", mode="interval",
                              transcript_id=body.transcript_id,
                              start=body.start, end=body.end,
                              fragments=out["result"]["fragment_count"])
        return out

    @app.post("/map/genomic-to-tx/interval")
    async def genomic_to_tx_interval(
        body: IntervalRequest, request: Request
    ) -> dict[str, object]:
        rid = resolve_request_id(request, body)
        request.state.request_id = rid
        out, _ = app.state.service.map_interval(
            body.transcript_id, "genomic_to_tx", body.start, body.end, rid
        )
        diagnostics.log_event(logging.INFO, "mapping_accepted", rid,
                              direction="genomic_to_tx", mode="interval",
                              transcript_id=body.transcript_id,
                              start=body.start, end=body.end,
                              fragments=out["result"]["fragment_count"])
        return out

    @app.get("/audit/{request_id}")
    async def audit_trail(request_id: str) -> dict[str, object]:
        rows = app.state.service.audit_trail(request_id)
        return {"request_id": request_id, "count": len(rows), "records": rows}

    return app


app = create_app()
