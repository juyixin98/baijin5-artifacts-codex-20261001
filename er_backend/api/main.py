"""FastAPI query/validation layer.

Maps the backend error taxonomy to HTTP status codes so failure classes
stay distinguishable over the wire:
  input_error -> 400, state_conflict -> 409,
  resource_exhausted -> 413, computation_failure -> 500.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..errors import ERCategory, ERError
from ..index.store import Store
from ..models import ConstraintSet, Lock, Record, ResolveResult
from ..service import ResolutionService

_STATUS_BY_CATEGORY = {
    ERCategory.INPUT_ERROR: 400,
    ERCategory.STATE_CONFLICT: 409,
    ERCategory.RESOURCE_EXHAUSTED: 413,
    ERCategory.COMPUTATION_FAILURE: 500,
}


class IngestRequest(BaseModel):
    records: list[Record] = Field(min_length=1)


class LockRequest(BaseModel):
    record_ids: list[str] = Field(min_length=1)
    note: str = ""


def load_token_map(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("token map must be a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def create_app(
    db_path: str = ":memory:",
    settings: Settings | None = None,
    token_map_path: str | None = None,
) -> FastAPI:
    settings = settings or Settings(db_path=db_path)
    store = Store(db_path)
    service = ResolutionService(store, settings, load_token_map(token_map_path))

    app = FastAPI(title="entity-resolution-backend", version="0.1.0")
    app.state.service = service

    @app.exception_handler(ERError)
    async def er_error_handler(_: Request, exc: ERError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_BY_CATEGORY[exc.category],
            content={"error": exc.to_dict()},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "category": ERCategory.INPUT_ERROR.value,
                    "code": "request_validation_failed",
                    "message": "request body failed schema validation",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    @app.post("/records")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        return service.ingest(req.records)

    @app.post("/constraints")
    def set_constraints(constraints: ConstraintSet) -> dict[str, Any]:
        return service.set_constraints(constraints)

    @app.post("/locks")
    def add_lock(req: LockRequest) -> dict[str, Any]:
        digest = hashlib.sha256(
            "|".join(sorted(req.record_ids)).encode()
        ).hexdigest()[:8]
        lock_id = f"lock-{digest}"
        return service.add_lock(
            Lock(lock_id=lock_id, record_ids=req.record_ids, note=req.note)
        )

    @app.post("/resolve")
    def resolve() -> ResolveResult:
        return service.resolve()

    @app.get("/clusters")
    def clusters() -> dict[str, Any]:
        run_id = store.latest_run_id()
        if run_id is None:
            return {"run_id": None, "clusters": []}
        assignment = store.load_assignment(run_id)
        groups: dict[str, list[str]] = {}
        for rid, cid in assignment.items():
            groups.setdefault(cid, []).append(rid)
        return {
            "run_id": run_id,
            "clusters": [
                {"cluster_id": cid, "record_ids": sorted(m)}
                for cid, m in sorted(groups.items())
            ],
        }

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return service.store.load_run(run_id)

    @app.post("/runs/{run_id}/replay")
    def replay(run_id: str) -> dict[str, Any]:
        return service.replay(run_id)

    return app
