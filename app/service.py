"""FastAPI HTTP boundary for the LORD 3 teaching service.

Only validation and transport live here; every statistical action delegates
to :mod:`app.contracts`, :mod:`app.store` and :mod:`app.diagnostics`.

Endpoints
---------
GET  /health
GET  /contract                      frozen rule, formula and assumptions
POST /runs                          create an empty run
GET  /runs                          list run ids
GET  /runs/{run_id}                 run state snapshot
POST /runs/{run_id}/decisions       submit ONE p-value, get its decision
GET  /runs/{run_id}/decisions       paged decision history
POST /runs/{run_id}/audit           independent replay + hash-chain audit
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder as _jsonable
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import (
    CONTRACT_FINGERPRINT,
    DEFAULT_MAX_DECISIONS,
    HARD_MAX_DECISIONS,
    RULE_VERSION,
    contract_manifest,
)
from .diagnostics import replay_verify
from .errors import (
    INVALID_QUERY,
    LORD3Error,
    MALFORMED_REQUEST,
    ErrorCategory,
)
from .store import SQLiteStore

logger = logging.getLogger("lord3.service")


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_decisions: int = Field(
        default=DEFAULT_MAX_DECISIONS,
        ge=1,
        le=HARD_MAX_DECISIONS,
    )


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    p_value: float

    @field_validator("p_value", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        # Pydantic's lax mode coerces bool -> float; a p-value is never a bool.
        if isinstance(value, bool):
            raise ValueError("p_value must be a number, not a boolean")
        return value


def _error_response(
    code: str, category: ErrorCategory, message: str,
    status_code: int, details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "category": category.value,
                "message": message,
                "details": details or {},
            }
        },
    )


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="LORD 3 online FDR teaching service",
        version="1.0.0",
        description=(
            "Fixed-sequence online FDR decisions under one frozen LORD 3 "
            "rule (Javanmard & Montanari, arXiv:1603.09000)."
        ),
    )
    path = db_path or os.environ.get("LORD3_DB", "data/lord3.db")
    if path != ":memory:":
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
    app.state.store = SQLiteStore(path)

    @app.exception_handler(LORD3Error)
    async def _handle_typed(_: Request, exc: LORD3Error) -> JSONResponse:
        logger.warning(
            "categorized_error code=%s category=%s details=%s",
            exc.error_code.code,
            exc.error_code.category.value,
            exc.details,
        )
        return JSONResponse(
            status_code=exc.error_code.http_status, content=exc.to_dict()
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            MALFORMED_REQUEST.code,
            MALFORMED_REQUEST.category,
            MALFORMED_REQUEST.message,
            MALFORMED_REQUEST.http_status,
            details={"validation": _jsonable(exc.errors())},
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "rule_version": RULE_VERSION,
            "contract_fingerprint": CONTRACT_FINGERPRINT,
        }

    @app.get("/contract")
    def contract() -> dict:
        return contract_manifest()

    @app.post("/runs", status_code=201)
    def create_run(body: CreateRunRequest) -> dict:
        meta = app.state.store.create_run(max_decisions=body.max_decisions)
        logger.info("run_created run_id=%s max_decisions=%s",
                    meta.run_id, meta.max_decisions)
        return _run_dict(meta)

    @app.get("/runs")
    def list_runs() -> dict:
        return {"run_ids": app.state.store.list_run_ids()}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return _run_dict(app.state.store.get_run(run_id))

    @app.post("/runs/{run_id}/decisions", status_code=201)
    def submit_decision(run_id: str, body: DecisionRequest) -> dict:
        record = app.state.store.append_decision(
            run_id, body.hypothesis_id, body.p_value
        )
        logger.info(
            "decision run_id=%s idx=%s hypothesis_id=%s p=%.12g "
            "threshold=%.12g rejected=%s wealth_after=%.12g reason=%s",
            run_id, record["idx"], record["hypothesis_id"],
            record["p_value"], record["threshold"],
            int(record["rejected"]), record["wealth_after"],
            record["reason"],
        )
        return record

    @app.get("/runs/{run_id}/decisions")
    def list_decisions(run_id: str, limit: int = 100, offset: int = 0) -> dict:
        if not (1 <= limit <= 1000) or offset < 0:
            return _error_response(
                INVALID_QUERY.code,
                INVALID_QUERY.category,
                INVALID_QUERY.message,
                INVALID_QUERY.http_status,
                details={"limit": limit, "offset": offset},
            )
        rows = app.state.store.list_decisions(run_id, limit=limit, offset=offset)
        return {"run_id": run_id, "limit": limit, "offset": offset,
                "decisions": rows}

    @app.post("/runs/{run_id}/audit")
    def audit_run(run_id: str) -> dict:
        report = replay_verify(
            app.state.store, run_id, raise_on_mismatch=False
        )
        logger.info(
            "audit run_id=%s ok=%s chain_ok=%s counters_ok=%s "
            "fingerprint_ok=%s n_decisions=%s mismatches=%s",
            run_id, report.ok, report.chain_ok, report.run_counters_ok,
            report.contract_fingerprint_ok, report.n_decisions,
            len(report.mismatches),
        )
        return report.to_dict()

    return app


def _run_dict(meta: Any) -> dict:
    return {
        "run_id": meta.run_id,
        "rule_version": meta.rule_version,
        "contract_fingerprint": meta.contract_fingerprint,
        "max_decisions": meta.max_decisions,
        "state": {
            "tau": meta.tau,
            "w_tau": meta.w_tau,
            "wealth": meta.wealth,
            "last_index": meta.last_index,
            "status": meta.status,
        },
        "created_at": meta.created_at,
    }


app = create_app()
