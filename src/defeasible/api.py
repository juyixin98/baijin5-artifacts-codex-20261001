"""FastAPI query interface.

Endpoints
---------
``POST /cases/{case_id}/theory``   upload (replace) the rule base
``PUT  /cases/{case_id}/evidence`` add ground evidence
``GET  /cases``                    list cases
``GET  /cases/{case_id}``          snapshot (theory + evidence)
``POST /cases/{case_id}/evaluate`` full conclusion table
``POST /cases/{case_id}/query``    one literal: status + support/defeat/pending chains
``GET  /runs/{run_id}``            replay a run (intermediate states + reasons)
``GET  /health``                   liveness probe

All four failure categories map to distinct HTTP statuses and bodies:

============================  ====
code                          HTTP
============================  ====
invalid_input                 422
state_conflict                409
resource_exhausted            509
computation_failure           500
============================  ====
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .errors import DefeasibleError
from .logging import RunLogger
from .service import ReasoningService
from .storage import EvidenceStore


# ---------------------------------------------------------------------------
# request / response schemas
# ---------------------------------------------------------------------------


class RuleIn(BaseModel):
    id: str
    kind: str = Field(description="'strict' or 'default'")
    body: list[str] = Field(default_factory=list)
    head: str
    label: str = ""


class PriorityIn(BaseModel):
    higher: str
    lower: str


class TheoryIn(BaseModel):
    rules: list[RuleIn]
    priorities: list[PriorityIn] = Field(default_factory=list)


class EvidenceIn(BaseModel):
    literals: list[str]


class QueryIn(BaseModel):
    literal: str


# ---------------------------------------------------------------------------
# app factory (explicit dependency wiring -> easy integration testing)
# ---------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    store: EvidenceStore | None = None,
    logger: RunLogger | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    from .engine import Engine

    app = FastAPI(
        title="Defeasible Reasoning API",
        version="1.0.0",
        description="Explainable defeasible reasoning with a restricted semantics.",
    )
    app.state.settings = settings
    app.state.store = store or EvidenceStore(settings.db_path)
    app.state.logger = logger or RunLogger(settings.log_path)
    app.state.service = ReasoningService(
        app.state.store, Engine(settings.engine_limits()), app.state.logger
    )

    @app.exception_handler(DefeasibleError)
    async def _handle_defined_error(_request: Any, exc: DefeasibleError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/cases")
    async def list_cases() -> dict:
        return {"cases": app.state.service.list_cases()}

    @app.get("/cases/{case_id}")
    async def snapshot(case_id: str) -> dict:
        return app.state.service.case_snapshot(case_id)

    @app.post("/cases/{case_id}/theory", status_code=200)
    async def put_theory(case_id: str, payload: TheoryIn) -> dict:
        return app.state.service.put_theory(case_id, payload.model_dump())

    @app.put("/cases/{case_id}/evidence", status_code=200)
    async def add_evidence(case_id: str, payload: EvidenceIn) -> dict:
        return app.state.service.add_evidence(case_id, payload.literals)

    @app.post("/cases/{case_id}/evaluate", status_code=200)
    async def evaluate(case_id: str) -> dict:
        ev = app.state.service.evaluate_case(case_id)
        return ev.to_dict()

    @app.post("/cases/{case_id}/query", status_code=200)
    async def query(case_id: str, payload: QueryIn) -> dict:
        return app.state.service.query(case_id, payload.literal)

    @app.get("/runs/{run_id}")
    async def replay(run_id: str) -> dict:
        return app.state.service.replay(run_id)

    return app


# Lazy module-level app so that merely importing this module (e.g. during
# test collection) does not create the default database/log directories.
# `uvicorn defeasible.api:app` resolves this through __getattr__.
def __getattr__(name: str) -> FastAPI:
    if name == "app":
        app = create_app()
        globals()["app"] = app
        return app
    raise AttributeError(name)
