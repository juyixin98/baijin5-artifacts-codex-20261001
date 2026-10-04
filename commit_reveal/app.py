"""FastAPI application: HTTP surface over the round service.

Every request gets a request id (``X-Request-ID`` response header) that is
threaded into the audit trail, so any accept/reject decision can be traced.
Protocol rejections are returned as structured errors with a stable failure
category; secrets are never logged in clear.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from commit_reveal.config import Settings, load_settings
from commit_reveal.errors import ProtocolError
from commit_reveal.services.round_service import RoundService
from commit_reveal.state.audit import AuditLog
from commit_reveal.state.db import connect
from commit_reveal.state.repository import Repository
from commit_reveal.verify.verifier import verify_evidence

logger = logging.getLogger("commit_reveal")


class CreateRoundRequest(BaseModel):
    round_id: str = Field(min_length=1, max_length=128)
    participants: list[str] = Field(min_length=2)
    commit_deadline: int
    reveal_deadline: int


class CommitRequest(BaseModel):
    participant_id: str
    commitment: str


class RevealRequest(BaseModel):
    participant_id: str
    random_value: str
    salt: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    conn = connect(settings.db_path)
    db_lock = threading.RLock()
    repo = Repository(conn, lock=db_lock)
    audit = AuditLog(conn, lock=db_lock)
    service = RoundService(repo, audit, clock=lambda: int(time.time()))

    app = FastAPI(title="commit-reveal-draw", version="0.1.0")
    app.state.service = service
    app.state.audit = audit

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ProtocolError)
    async def protocol_error_handler(request: Request, exc: ProtocolError):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.info(
            "rejected request_id=%s path=%s code=%s reason=%s",
            request_id, request.url.path, exc.code.value, exc.reason,
        )
        status = 404 if exc.code.value == "ROUND_NOT_FOUND" else 409
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code.value, "reason": exc.reason,
                               "request_id": request_id}},
        )

    @app.post("/rounds", status_code=201)
    def create_round(body: CreateRoundRequest, request: Request):
        service.create_round(
            request.state.request_id, body.round_id, body.participants,
            body.commit_deadline, body.reveal_deadline,
        )
        return service.get_round_view(body.round_id)

    @app.get("/rounds/{round_id}")
    def get_round(round_id: str):
        return service.get_round_view(round_id)

    @app.post("/rounds/{round_id}/commitments", status_code=201)
    def commit(round_id: str, body: CommitRequest, request: Request):
        service.commit(request.state.request_id, round_id,
                       body.participant_id, body.commitment)
        return {"accepted": True}

    @app.post("/rounds/{round_id}/reveals", status_code=201)
    def reveal(round_id: str, body: RevealRequest, request: Request):
        service.reveal(request.state.request_id, round_id,
                       body.participant_id, body.random_value, body.salt)
        return {"accepted": True}

    @app.post("/rounds/{round_id}/finalize")
    def finalize(round_id: str, request: Request):
        return service.finalize(request.state.request_id, round_id)

    @app.get("/rounds/{round_id}/evidence")
    def evidence(round_id: str):
        return service.get_evidence(round_id)

    @app.post("/rounds/{round_id}/verify")
    def verify(round_id: str):
        return verify_evidence(service.get_evidence(round_id)).to_dict()

    @app.get("/rounds/{round_id}/audit")
    def audit_trail(round_id: str):
        return {"events": audit.list_for_round(round_id)}

    return app


app = create_app()
