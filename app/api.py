"""FastAPI HTTP layer.

Validation failures are returned *in-band* in the evidence envelope with
``status: "error"`` and a structured ``failures`` list (HTTP 200), so callers
always receive the request id and the failure taxonomy.  Genuine malformed
JSON yields HTTP 400; unknown routes 404.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import SETTINGS, Settings
from .evidence import EvidenceRecord
from .service import InferenceService
from .storage import Storage


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or SETTINGS
    app = FastAPI(
        title="Paired Randomization Inference API",
        version=__version__,
        description="Exact/approximate paired randomization tests with constant-effect inversion.",
    )
    app.state.settings = settings
    app.state.service = InferenceService(settings)
    app.state.storage = Storage(settings.db_path)

    async def _payload(request: Request) -> Optional[Dict[str, Any]]:
        try:
            body = await request.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    def _run(endpoint: str, method: str, payload: Dict[str, Any], worker, storage: Storage):
        evidence = EvidenceRecord()
        result = None
        computation: Optional[str] = None
        certified: Optional[bool] = None
        try:
            result = worker(payload, evidence)
            if result is not None:
                computation = result.get("kind")
                certified = result.get("certified")
        except Exception as exc:  # defensive: never leak a raw 500
            evidence.fail("internal_error", f"unexpected error: {exc}", "app.api:_run")
        envelope = evidence.envelope(result)
        storage.record_request(
            evidence.request_id, endpoint, method, payload, envelope, computation, certified
        )
        return envelope

    @app.exception_handler(404)
    async def not_found(request: Request, exc):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=404, content={"status": "error", "failures": ["not found"]})

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok", "service_version": __version__}

    @app.post("/api/pvalue")
    async def pvalue(request: Request):
        payload = await _payload(request)
        if payload is None:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "failures": [{"code": "invalid_pairs", "message": "request body must be a JSON object"}]},
            )
        return _run(
            "/api/pvalue", "two-sided randomization p-value", payload,
            app.state.service.p_value, app.state.storage,
        )

    @app.post("/api/invert")
    async def invert(request: Request):
        payload = await _payload(request)
        if payload is None:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "failures": [{"code": "invalid_pairs", "message": "request body must be a JSON object"}]},
            )
        return _run(
            "/api/invert", "constant-effect acceptance-set inversion", payload,
            app.state.service.invert, app.state.storage,
        )

    @app.post("/api/randomization-set")
    async def randomization_set(request: Request):
        payload = await _payload(request)
        if payload is None:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "failures": [{"code": "invalid_pairs", "message": "request body must be a JSON object"}]},
            )
        return _run(
            "/api/randomization-set", "paired randomization-set preview", payload,
            app.state.service.randomization_preview, app.state.storage,
        )

    @app.get("/api/requests/{request_id}")
    async def get_request(request_id: str) -> Dict[str, Any]:
        row = app.state.storage.fetch_request(request_id)
        if row is None:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "failures": [{"code": "not_found", "message": request_id}]},
            )
        return {"status": "ok", "request": row}

    @app.get("/api/requests")
    async def list_requests() -> Dict[str, Any]:
        return {"status": "ok", "requests": app.state.storage.list_requests()}

    return app


app = create_app()
