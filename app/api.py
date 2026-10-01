"""FastAPI application: online FDR teaching service.

Endpoints
    POST /runs                       create a run with FROZEN parameters
    GET  /runs/{run_id}              run status
    POST /runs/{run_id}/reserve      phase 1: pre-commit alpha_t
    POST /runs/{run_id}/decide       phase 2: submit p_t, get decision
    GET  /runs/{run_id}/steps        full history
    GET  /runs/{run_id}/replay       recompute + hash-chain audit
    GET  /runs/{run_id}/events       append-only diagnostic event log
    GET  /contract                   frozen statistical contract

All domain failures return a consistent envelope
    {"success": false, "error": {"code", "message", "details"}}
with codes INPUT_ERROR / STATE_CONFLICT / RESOURCE_EXHAUSTED /
COMPUTATION_FAILED / NOT_FOUND / INTEGRITY_ERROR.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import (
    DomainError,
    ErrorCode,
    HTTP_STATUS,
    InputValidationError,
)
from .schemas import (
    DecideRequest,
    EventOut,
    ReplayOut,
    ReplayStep,
    ReserveRequest,
    RunCreateRequest,
    RunOut,
    StepOut,
    contract_payload,
)
from .statistics import LordConfig
from .storage import RunStore


def create_app(db_path: str | None = None) -> FastAPI:
    """Application factory; one store instance lives on app.state."""
    path = db_path if db_path is not None else os.environ.get("FDR_DB_PATH", ":memory:")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _app.state.store = RunStore(path)
        try:
            yield
        finally:
            _app.state.store.close()

    app = FastAPI(
        title="Online FDR Teaching Service (LORD++)",
        version="1.0.0",
        description="Causal online multiple-testing budget with frozen LORD++ rule.",
        lifespan=lifespan,
    )

    # ----------------------------------------------------- dependencies --

    def _resolve_store(request: Request) -> RunStore:
        """Resolve the store honoring a test dependency override.

        Production dependency takes the request; the documented zero-arg
        override form must also work from the exception handler.
        """
        override = request.app.dependency_overrides.get(get_store)
        if override is not None:
            return override()
        return get_store(request)

    # ----------------------------------------------------- error envelope --

    @app.exception_handler(DomainError)
    async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        run_id = request.path_params.get("run_id")
        try:
            _resolve_store(request).log_error(
                run_id, exc.code, exc.message, exc.details
            )
        except Exception:  # pragma: no cover - logging must never mask the error
            pass
        return _error_envelope(exc)

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        err = InputValidationError(
            "request payload failed validation",
            details={"errors": _sanitize_validation_errors(exc.errors())},
        )
        return _error_envelope(err)

    # --------------------------------------------------------- contract --

    @app.get("/contract")
    def get_contract() -> dict[str, Any]:
        payload = contract_payload()
        return {"success": True, "data": payload.model_dump()}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------- runs --

    @app.post("/runs", status_code=201)
    def create_run(
        body: RunCreateRequest, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        config = LordConfig.create(
            alpha=body.alpha if body.alpha is not None else LordConfig().alpha,
            w0=body.w0,
            horizon=body.horizon if body.horizon is not None else LordConfig().horizon,
        )
        rid = store.create_run(config, note=body.note, run_id=body.run_id)
        run = store.get_run(rid)
        out = RunOut(
            run_id=run["run_id"],
            contract_version=run["contract_version"],
            alpha=run["alpha"],
            w0=run["w0"],
            payoff=config.payoff,
            horizon=run["horizon"],
            status=run["status"],
            note=run["note"],
            head_hash=run["head_hash"],
        )
        return {"success": True, "data": out.model_dump()}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, store: RunStore = Depends(get_store)) -> dict[str, Any]:
        run = store.get_run(run_id)
        config = store.config_of(run_id)
        out = RunOut(
            run_id=run["run_id"],
            contract_version=run["contract_version"],
            alpha=run["alpha"],
            w0=run["w0"],
            payoff=config.payoff,
            horizon=run["horizon"],
            status=run["status"],
            note=run["note"],
            head_hash=run["head_hash"],
        )
        return {"success": True, "data": out.model_dump()}

    # ----------------------------------------------------------- decide --

    @app.post("/runs/{run_id}/reserve", status_code=201)
    def reserve(
        run_id: str, body: ReserveRequest, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        row = store.reserve(run_id, body.hypothesis_id)
        return {"success": True, "data": _step_out(row).model_dump()}

    @app.post("/runs/{run_id}/decide")
    def decide(
        run_id: str, body: DecideRequest, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        row = store.decide(run_id, body.hypothesis_id, body.p_value)
        return {"success": True, "data": _step_out(row).model_dump()}

    @app.get("/runs/{run_id}/steps")
    def list_steps(
        run_id: str, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        rows = store.list_steps(run_id)
        return {"success": True, "data": [_step_out(r).model_dump() for r in rows]}

    @app.get("/runs/{run_id}/replay")
    def replay(
        run_id: str, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        report = store.replay(run_id)
        out = ReplayOut(
            run_id=report["run_id"],
            contract_version=report["contract_version"],
            decisions_checked=report["decisions_checked"],
            rejections=report["rejections"],
            head_hash=report["head_hash"],
            steps=[ReplayStep(**s) for s in report["steps"]],
        )
        return {"success": True, "data": out.model_dump()}

    @app.get("/runs/{run_id}/events")
    def list_events(
        run_id: str, limit: int = 200, store: RunStore = Depends(get_store)
    ) -> dict[str, Any]:
        raw = store.list_events(run_id, limit=min(max(limit, 1), 1000))
        events: list[EventOut] = []
        for r in raw:
            events.append(
                EventOut(
                    seq=r["seq"],
                    kind=r["kind"],
                    code=r["code"],
                    message=r["message"],
                    payload=json.loads(r["payload"]),
                    created_at=r["created_at"],
                )
            )
        return {"success": True, "data": [e.model_dump() for e in events]}

    return app


def get_store(request: Request) -> RunStore:
    """FastAPI dependency: the per-app store created in the lifespan.

    Defined at module level (rather than nested in the factory) so tests can
    override exactly this callable via ``app.dependency_overrides``.
    """
    return request.app.state.store


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pydantic v2 errors embed raw exception objects (ctx) that are not JSON
    serializable. Keep loc/type/msg/input, coerce everything else to strings.
    """
    safe: list[dict[str, Any]] = []
    for e in errors:
        item: dict[str, Any] = {}
        for key in ("loc", "type", "msg"):
            if key in e:
                item[key] = e[key]
        if "input" in e:
            item["input"] = _json_safe(e["input"])
        if "ctx" in e and e["ctx"]:
            item["ctx"] = {k: _json_safe(v) for k, v in e["ctx"].items()}
        safe.append(item)
    return safe


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _error_envelope(exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[exc.code],
        content={"success": False, "error": exc.to_dict()},
    )


def _step_out(row: dict[str, Any]) -> StepOut:
    return StepOut(
        run_id=row["run_id"],
        idx=row["idx"],
        hypothesis_id=row["hypothesis_id"],
        threshold=row["threshold"],
        wealth_before=row["wealth_before"],
        gamma_t=row["gamma_t"],
        status=row["status"],
        p_value=row["p_value"],
        rejected=None if row["rejected"] is None else bool(row["rejected"]),
        wealth_after=row["wealth_after"],
        reserved_at=row["reserved_at"],
        decided_at=row["decided_at"],
        prev_hash=row["prev_hash"],
        row_hash=row["row_hash"],
    )


# Default ASGI app instance (uses FDR_DB_PATH or an in-memory store).
app = create_app()
