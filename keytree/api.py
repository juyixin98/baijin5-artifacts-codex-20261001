"""FastAPI HTTP boundary.

Maps the service error taxonomy to HTTP status codes:

    input_error        -> 400
    state_conflict     -> 409
    resource_exhausted -> 413
    computation_failure-> 500

Every response (success or error) carries the run_id so a failing request
can be replayed from the diagnostic log and the audit table.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .crypto_adapter import CryptographyHkdf
from .errors import (
    ComputationError,
    InputValidationError,
    KeyTreeError,
    ResourceExhaustedError,
    StateConflictError,
)
from .identity import KeyIdentity
from .logging_config import new_run_id, open_log_stream
from .service import KeyTreeService
from .store import Store

_STATUS_BY_CATEGORY = {
    InputValidationError.category: 400,
    StateConflictError.category: 409,
    ResourceExhaustedError.category: 413,
    ComputationError.category: 500,
}


class DeriveRequest(BaseModel):
    tenant: str
    purpose: str
    version: int = Field(ge=0)
    context_b64: str = ""
    length: int = 32
    display_name: str | None = None


class BindRequest(BaseModel):
    display_name: str
    key_id: str


def _decode_context(context_b64: str) -> bytes:
    try:
        return base64.b64decode(context_b64.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise InputValidationError(
            "context_b64 is not valid base64", details={"error": str(exc)}
        ) from exc


def create_app(
    db_path: str | Path,
    log_path: str | Path | None = None,
    root_hex: str | None = None,
) -> FastAPI:
    store = Store(db_path)
    log_stream = open_log_stream(log_path) if log_path else None
    backend = CryptographyHkdf()
    if root_hex is not None:
        # Seed the fixed root (local testing only) before serving requests.
        KeyTreeService(store, backend=backend, root_hex=root_hex)
    app = FastAPI(title="keytree", version="0.1.0")

    @app.exception_handler(KeyTreeError)
    async def keytree_error_handler(request: Request, exc: KeyTreeError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None) or new_run_id()
        status = _STATUS_BY_CATEGORY.get(exc.category, 500)
        return JSONResponse(
            status_code=status,
            content={"error": {**exc.to_dict(), "run_id": run_id}},
        )

    @app.post("/v1/derive")
    def derive(req: DeriveRequest) -> dict:
        run_id = new_run_id()
        svc = KeyTreeService(store, backend=backend, log_stream=log_stream)
        identity = KeyIdentity(
            tenant=req.tenant,
            purpose=req.purpose,
            version=req.version,
            context=_decode_context(req.context_b64),
        )
        result = svc.derive(
            identity,
            length=req.length,
            display_name=req.display_name,
            run_id=run_id,
        )
        return {
            "run_id": result.run_id,
            "key_id": result.key_id,
            "key_hex": result.key_bytes.hex(),
            "fingerprint": result.fingerprint,
            "length": result.length,
        }

    @app.post("/v1/names")
    def bind_name(req: BindRequest) -> dict:
        store.bind_name(req.display_name, req.key_id)
        return {"display_name": req.display_name, "key_id": req.key_id}

    @app.get("/v1/keys/{key_id}")
    def get_key(key_id: str) -> dict:
        row = store.get_registered(key_id)
        if row is None:
            raise InputValidationError(
                "unknown key_id", details={"key_id": key_id}
            )
        # Metadata only — key material is never served from the registry.
        return {
            "key_id": row["key_id"],
            "display_name": row["display_name"],
            "fingerprint": row["fingerprint"],
            "length": row["length"],
            "created_run": row["created_run"],
        }

    @app.get("/v1/audit/{run_id}")
    def get_audit(run_id: str) -> dict:
        return {"run_id": run_id, "events": store.audit_for_run(run_id)}

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
