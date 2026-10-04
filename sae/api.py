"""HTTP API (FastAPI).

Thin transport layer over :class:`sae.service.ChunkedCryptoService`.
Every request gets a request id (``X-Request-ID`` header or generated)
which is echoed in responses and written to the audit trail.  Errors
are reported with their stable category so clients can react to the
*kind* of failure.
"""

from __future__ import annotations

import base64
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .errors import SAEError
from .service import ChunkedCryptoService


class CreateMessageIn(BaseModel):
    message_id: str | None = None
    salt_hex: str | None = None
    nonce_base_hex: str | None = None


class ChunkIn(BaseModel):
    seq: int = Field(ge=0)
    final: bool = False
    plaintext_b64: str


class CreateMessageOut(BaseModel):
    message_id: str


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data, validate=True)


def create_app(settings: Settings | None = None,
               service: ChunkedCryptoService | None = None) -> FastAPI:
    if service is None:
        service = ChunkedCryptoService(settings or Settings.from_env())
    app = FastAPI(title="sae", version="0.1.0")
    app.state.service = service

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(SAEError)
    async def sae_error_handler(request: Request, exc: SAEError):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "category": exc.category,
                    "reason": exc.reason,
                    "context": exc.context,
                },
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {"category": "bad_request", "reason": str(exc),
                          "context": {}},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    def rid(request: Request) -> str:
        return request.state.request_id

    @app.post("/v1/messages", response_model=CreateMessageOut, status_code=201)
    def create_message(body: CreateMessageIn, request: Request):
        return service.create_message(
            request_id=rid(request),
            message_id=body.message_id,
            salt=bytes.fromhex(body.salt_hex) if body.salt_hex else None,
            nonce_base=bytes.fromhex(body.nonce_base_hex) if body.nonce_base_hex else None,
        )

    @app.post("/v1/messages/{message_id}/chunks", status_code=201)
    def submit_chunk(message_id: str, body: ChunkIn, request: Request):
        result = service.submit_chunk(
            request_id=rid(request),
            message_id=message_id,
            seq=body.seq,
            final=body.final,
            plaintext=_b64d(body.plaintext_b64),
        )
        return {
            "message_id": result["message_id"],
            "seq": result["seq"],
            "ciphertext_b64": _b64e(result["ciphertext"]),
            "replayed": result["replayed"],
        }

    @app.post("/v1/messages/{message_id}/finalize")
    def finalize(message_id: str, request: Request):
        return service.finalize(request_id=rid(request), message_id=message_id)

    @app.get("/v1/messages/{message_id}")
    def status(message_id: str):
        return service.get_status(message_id)

    @app.get("/v1/messages/{message_id}/plaintext")
    def plaintext(message_id: str, request: Request):
        data = service.get_plaintext(request_id=rid(request), message_id=message_id)
        return {"message_id": message_id, "plaintext_b64": _b64e(data)}

    @app.get("/v1/messages/{message_id}/audit")
    def audit(message_id: str):
        return {"message_id": message_id, "events": service.audit.for_message(message_id)}

    return app
