"""FastAPI surface for the derivation service.

The API layer translates the service's error taxonomy into HTTP status
codes and never exposes key material beyond the explicit derive response:

- ``input``              -> 400
- ``state_conflict``     -> 409
- ``resource_exhausted`` -> 413
- ``computation``        -> 500
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .crypto_adapter import MAX_DERIVE_LEN
from .errors import (
    CATEGORY_COMPUTATION,
    CATEGORY_INPUT,
    CATEGORY_RESOURCE_EXHAUSTED,
    CATEGORY_STATE_CONFLICT,
    KdsError,
)
from .identity import KeyIdentity
from .service import DerivationTreeService
from .state import StateStore

_STATUS_BY_CATEGORY = {
    CATEGORY_INPUT: 400,
    CATEGORY_STATE_CONFLICT: 409,
    CATEGORY_RESOURCE_EXHAUSTED: 413,
    CATEGORY_COMPUTATION: 500,
}


class IdentityIn(BaseModel):
    tenant: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    context: str = Field(min_length=1, max_length=64)


class RegisterIn(IdentityIn):
    display_name: str = Field(min_length=1, max_length=128)


class DeriveIn(IdentityIn):
    length: int = Field(default=32, ge=1, le=MAX_DERIVE_LEN)


def _to_identity(body: IdentityIn) -> KeyIdentity:
    return KeyIdentity(
        tenant=body.tenant,
        purpose=body.purpose,
        version=body.version,
        context=body.context,
    )


def create_app(service: DerivationTreeService, store: StateStore) -> FastAPI:
    app = FastAPI(title="kds", version="0.1.0")

    @app.exception_handler(KdsError)
    async def _kds_error_handler(_: Request, exc: KdsError) -> JSONResponse:
        status = _STATUS_BY_CATEGORY.get(exc.category, 500)
        return JSONResponse(status_code=status, content=exc.to_dict())

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/keys/register", status_code=201)
    def register(body: RegisterIn) -> dict:
        identity = _to_identity(body)
        key_id = service.register(identity, body.display_name)
        return {"key_id": key_id}

    @app.post("/v1/keys/derive")
    def derive(body: DeriveIn) -> dict:
        identity = _to_identity(body)
        derived = service.derive(identity, length=body.length)
        return {
            "key_id": derived.key_id,
            "key_hex": derived.key.hex(),
            "length": derived.length,
            "run_id": derived.run_id,
        }

    @app.get("/v1/keys/{key_id}")
    def get_key(key_id: str) -> dict:
        record = store.get_key(key_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown key id")
        return record

    @app.get("/v1/audit")
    def audit() -> dict:
        return {"entries": store.list_audit()}

    return app
