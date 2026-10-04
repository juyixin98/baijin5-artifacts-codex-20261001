"""FastAPI HTTP adapter exposing the SCRAM-SHA-256 server state machine.

Endpoints (loopback test service):

* ``GET  /healthz``                 liveness and negotiated configuration;
* ``POST /v1/auth/scram/first``     submit client-first, receive server-first;
* ``POST /v1/auth/scram/final``     submit client-final, receive ``v=``.

Every request is tied to a correlation id: clients may send ``X-Request-ID``
(re-exported verbatim when it is a short printable token), otherwise the
server mints one and returns it in the response header and body.  Failures
always use the envelope::

    {"success": false, "request_id": "...", "error": {"category": ..., ...}}

with HTTP status chosen by failure category; security-sensitive outcomes
(invalid-proof, replay) are additionally rate limited per client peer.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLogger
from .config import AppConfig, load_config
from .errors import (
    ChannelBindingMismatch,
    FailureCategory,
    ScramError,
    UnsupportedChannelBinding,
)
from .server import ScramServerStateMachine
from .verifiers import VerifierRepository

LOCATION = "src/scram_auth/app.py"

# Categories that signal an attack/abuse and therefore feed the rate limiter.
_ABUSE_CATEGORIES = {
    FailureCategory.NONCE_REPLAY,
    FailureCategory.INVALID_PROOF,
    FailureCategory.PROTOCOL_VIOLATION,
    FailureCategory.CHANNEL_BINDING_MISMATCH,
}


class ClientFirstBody(BaseModel):
    message: str = Field(min_length=1, max_length=4096)


class ClientFinalBody(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=4096)


def _http_status_for(category: FailureCategory) -> int:
    return {
        FailureCategory.PROTOCOL_VIOLATION: 400,
        FailureCategory.INVALID_ENCODING: 400,
        FailureCategory.UNSUPPORTED_MECHANISM: 400,
        FailureCategory.UNSUPPORTED_CHANNEL_BINDING: 501,
        FailureCategory.CHANNEL_BINDING_MISMATCH: 401,
        FailureCategory.INVALID_PROOF: 401,
        FailureCategory.SERVER_SIGNATURE_INVALID: 401,
        FailureCategory.SESSION_NOT_FOUND: 404,
        FailureCategory.SESSION_EXPIRED: 404,
        FailureCategory.SESSION_REUSE: 410,
        FailureCategory.NONCE_REPLAY: 401,
        FailureCategory.NONCE_MISMATCH: 401,
        FailureCategory.WEAK_PARAMETERS: 400,
        FailureCategory.RATE_LIMITED: 429,
    }.get(category, 500)


class _SlidingWindowRateLimiter:
    """Minimal in-memory sliding-window limiter, keyed by client peer."""

    def __init__(self, max_events: int = 20, window_seconds: float = 60.0) -> None:
        self._max = max_events
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._events[key]
            while bucket and now - bucket[0] > self._window:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True


def _request_id(incoming: str | None) -> tuple[str, list[str]]:
    """Return (request_id, uncertain_notes).

    A supplied but malformed correlation id is an *inconclusive* condition
    (we cannot attribute the request to the caller's id) rather than a hard
    failure: the exchange proceeds under a freshly minted id, and the reason
    is reported separately as an uncertain observation.
    """
    if incoming:
        token = incoming.strip()
        if 8 <= len(token) <= 64 and all(c.isalnum() or c in "-_." for c in token):
            return token, []
        return uuid.uuid4().hex, [
            "supplied X-Request-ID failed validation (8-64 chars, alnum/-_/.); "
            "server minted a replacement correlation id"
        ]
    return uuid.uuid4().hex, []


def _parse_cert_hash(hex_value: str, *, plus_enabled: bool) -> bytes:
    """Decode the tls-server-end-point certificate hash header."""
    if not plus_enabled:
        raise UnsupportedChannelBinding(
            "certificate hash supplied while server channel-binding mode is 'none'"
        )
    try:
        digest = bytes.fromhex(hex_value)
    except ValueError as exc:
        raise ChannelBindingMismatch(
            "X-Tls-Server-End-Point-Sha256 must be hexadecimal", detail={"length": len(hex_value)}
        ) from exc
    if len(digest) != 32:
        raise ChannelBindingMismatch(
            "tls-server-end-point hash must be a 32-byte SHA-256 digest",
            detail={"length": len(digest)},
        )
    return digest


def create_app(config: AppConfig | None = None, *, audit: AuditLogger | None = None) -> FastAPI:
    """Application factory: wiring happens here; tests pass their own config."""
    config = config or load_config()
    audit = audit or AuditLogger(config.server.audit_log_path)
    repository = VerifierRepository(config.server.database_path)
    server = ScramServerStateMachine(config, repository, audit)
    limiter = _SlidingWindowRateLimiter()

    app = FastAPI(
        title="SCRAM-SHA-256 local test auth service",
        version="1.0.0",
        description="Loopback-only test service implementing RFC 5802 + RFC 7677.",
    )
    app.state.config = config
    app.state.audit = audit
    app.state.repository = repository
    app.state.server = server

    def _error_response(request_id: str, err: ScramError, status_code: int) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"success": False, "request_id": request_id, "error": err.to_dict()},
            headers={"X-Request-ID": request_id},
        )

    def _abuse_gate(request: Request, request_id: str, err: ScramError) -> JSONResponse:
        peer = request.client.host if request.client else "unknown"
        if err.category in _ABUSE_CATEGORIES:
            if not limiter.check_and_record(peer):
                rate_limited = ScramError(
                    "too many authentication failures from this peer",
                    detail={"retry_after_seconds": 60},
                )
                rate_limited.category = FailureCategory.RATE_LIMITED
                audit.failure(
                    request_id,
                    event="auth_rate_limited",
                    step="sliding_window",
                    failure=rate_limited.to_dict(),
                    location=LOCATION,
                )
                return _error_response(request_id, rate_limited, 429)
        audit.emit(
            AuditEvent(
                request_id=request_id,
                event="http_error_envelope",
                step=type(err).__name__,
                outcome="failure",
                failure=err.to_dict(),
                location=LOCATION,
            )
        )
        return _error_response(request_id, err, _http_status_for(err.category))

    @app.middleware("http")
    async def correlate(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id, uncertain_notes = _request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        audit.info(
            request_id,
            event="http_request",
            step=request.url.path,
            location=LOCATION,
            detail={
                "method": request.method,
                "path": request.url.path,
                "peer": request.client.host if request.client else "?",
            },
        )
        if uncertain_notes:
            audit.uncertain(
                request_id,
                event="correlation_id_reassigned",
                step="x-request-id",
                notes=uncertain_notes,
                location=LOCATION,
            )
        try:
            response = await call_next(request)
        except Exception:
            audit.failure(
                request_id,
                event="http_failure",
                step="unhandled_exception",
                failure={
                    "category": FailureCategory.INTERNAL_ERROR.value,
                    "message": "unhandled exception",
                },
                location=LOCATION,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        cfg = request.app.state.config
        return {
            "status": "ok",
            "request_id": request.state.request_id,
            "service": "scram-auth-local",
            "version": "1.0.0",
            "mechanism": cfg.crypto.mechanism,
            "channel_binding_scope": (
                ["n", "y", f"p={cfg.channel_binding.mode}"] if cfg.offers_plus else ["n", "y"]
            ),
            "iteration_count": cfg.crypto.iteration_count,
            "location": LOCATION,
        }

    @app.post("/v1/auth/scram/first")
    async def scram_first(
        request: Request,
        body: ClientFirstBody,
        x_tls_server_end_point_sha256: str | None = Header(default=None),
    ) -> JSONResponse:
        request_id = request.state.request_id
        cfg = request.app.state.config
        try:
            cert_hash = (
                _parse_cert_hash(
                    x_tls_server_end_point_sha256, plus_enabled=cfg.offers_plus
                )
                if x_tls_server_end_point_sha256
                else None
            )
            challenge = server.receive_client_first(
                body.message, request_id=request_id, tls_endpoint_hash=cert_hash
            )
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "request_id": request_id,
                    "session_id": challenge.session_id,
                    "server_first": challenge.message,
                },
                headers={"X-Request-ID": request_id},
            )
        except ScramError as err:
            return _abuse_gate(request, request_id, err)

    @app.post("/v1/auth/scram/final")
    async def scram_final(request: Request, body: ClientFinalBody) -> JSONResponse:
        request_id = request.state.request_id
        try:
            result = server.receive_client_final(
                body.message, session_id=body.session_id, request_id=request_id
            )
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "request_id": request_id,
                    "session_id": result.session_id,
                    "username": result.username,
                    "server_final": result.message,
                },
                headers={"X-Request-ID": request_id},
            )
        except ScramError as err:
            return _abuse_gate(request, request_id, err)

    return app
