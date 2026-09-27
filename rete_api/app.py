"""FastAPI query/control interface for the Rete engine.

Run with::

    uvicorn rete_api.app:app --reload

Every error response carries an explicit machine-readable ``category``; an
unknown/exceptional state is never returned as success.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from rete import (CycleLimitError, DuplicateRuleError, FactNotFoundError,
                  RuleError, RuleNotFoundError, __version__, rule_from_dict)
from rete.config import EngineConfig
from rete.store import EvidenceStore

from .schemas import FactIn, RuleIn, RunIn
from .service import SessionManager

log = logging.getLogger("rete.api")

# Exception category -> HTTP status. Never collapse these into 200.
_STATUS = {
    RuleError: (422, "rule_error"),
    DuplicateRuleError: (409, "duplicate_rule"),
    RuleNotFoundError: (404, "rule_not_found"),
    FactNotFoundError: (404, "fact_not_found"),
    CycleLimitError: (422, "cycle_limit_invalid"),
    ValueError: (422, "invalid_fact"),
    KeyError: (404, "session_not_found"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = os.environ.get("RETE_DB_PATH", "rete_evidence.db")
    app.state.store = EvidenceStore(db_path)
    app.state.manager = SessionManager(
        app.state.store,
        EngineConfig(
            default_max_cycles=int(os.environ.get("RETE_DEFAULT_MAX_CYCLES",
                                                  "100")),
            max_cycles_limit=int(os.environ.get("RETE_MAX_CYCLES_LIMIT",
                                                "100000"))))
    log.info("rete api starting version=%s db=%s", __version__, db_path)
    yield
    app.state.store.close()


app = FastAPI(title="Structured Rete Engine API", version=__version__,
              lifespan=lifespan)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Explicit failure: log with correlation data, return 500 with a
    # category instead of pretending success.
    log.exception("unhandled error path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "category": "internal_error",
                 "detail": str(exc)})


def _manager(request: Request) -> SessionManager:
    return request.app.state.manager


def _session(request: Request, session_id: str):
    try:
        return _manager(request).get(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={"error": "session_not_found",
                    "category": "session_not_found",
                    "detail": f"session {session_id!r} does not exist"})


def _raise(exc: Exception) -> None:
    for cls, (status, category) in _STATUS.items():
        if isinstance(exc, cls):
            raise HTTPException(
                status_code=status,
                detail={"error": type(exc).__name__, "category": category,
                        "detail": str(exc)})
    raise exc


# ---------------------------------------------------------------------------
# Metadata / sessions
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz(request: Request):
    return {"status": "ok", "engine_version": __version__}


@app.post("/sessions", status_code=201)
def create_session(request: Request, body: dict | None = None):
    sid = (body or {}).get("session_id") or uuid.uuid4().hex[:12]
    if not isinstance(sid, str) or not sid:
        raise HTTPException(status_code=422, detail={
            "error": "invalid_session_id", "category": "invalid_request",
            "detail": "session_id must be a non-empty string"})
    session = _manager(request).create(sid)
    log.info("session created session=%s", sid)
    return {"session_id": session.id, "engine_version": __version__}


@app.get("/sessions")
def list_sessions(request: Request):
    return {"sessions": _manager(request).list(),
            "engine_version": __version__}


@app.get("/sessions/{session_id}")
def get_session(request: Request, session_id: str):
    session = _session(request, session_id)
    eng = session.engine
    return {"session_id": session.id, "engine_version": __version__,
            "rules": eng.rules(), "fact_count": len(eng.facts),
            "agenda_size": len(eng.agenda)}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(request: Request, session_id: str):
    _manager(request).delete(session_id)
    log.info("session deleted session=%s", session_id)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/rules", status_code=201)
def add_rule(request: Request, session_id: str, body: RuleIn):
    session = _session(request, session_id)
    rule_dict = body.model_dump()
    # Parse first so malformed rules are rejected before touching state.
    try:
        rule = rule_from_dict(rule_dict)
    except RuleError as e:
        _raise(e)
    with session.lock:
        try:
            session.engine.add_rule(rule)
            _manager(request).record_rule(session, rule_dict)
        except (RuleError, DuplicateRuleError) as e:
            _raise(e)
    log.info("rule added session=%s rule=%s salience=%d conds=%d",
             session_id, rule.name, rule.salience, len(rule.conditions))
    return {"session_id": session_id, "rule": rule.name,
            "conditions": len(rule.conditions), "salience": rule.salience}


@app.get("/sessions/{session_id}/rules")
def list_rules(request: Request, session_id: str):
    session = _session(request, session_id)
    return {"session_id": session_id, "rules": session.engine.rules()}


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/facts", status_code=201)
def insert_fact(request: Request, session_id: str, body: FactIn):
    session = _session(request, session_id)
    with session.lock:
        try:
            wme, created = session.engine.insert(body.kind, tuple(body.fields))
        except ValueError as e:
            _raise(e)
    return {"session_id": session_id, "wme_id": wme.id, "kind": wme.kind,
            "fields": list(wme.fields), "refcount": wme.count,
            "created": created, "agenda_size": len(session.engine.agenda)}


@app.delete("/sessions/{session_id}/facts")
def retract_fact(request: Request, session_id: str, body: FactIn):
    session = _session(request, session_id)
    with session.lock:
        try:
            wme = session.engine.retract(body.kind, tuple(body.fields))
        except (FactNotFoundError, ValueError) as e:
            _raise(e)
    return {"session_id": session_id, "wme_id": wme.id, "kind": wme.kind,
            "fields": list(wme.fields), "refcount": wme.count,
            "removed": wme.count == 0,
            "agenda_size": len(session.engine.agenda)}


@app.get("/sessions/{session_id}/facts")
def list_facts(request: Request, session_id: str):
    session = _session(request, session_id)
    return {"session_id": session_id, "facts": [
        {"wme_id": w.id, "kind": w.kind, "fields": list(w.fields),
         "refcount": w.count}
        for w in sorted(session.engine.facts.distinct_facts(),
                        key=lambda w: w.id)]}


# ---------------------------------------------------------------------------
# Matching and bounded runs
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/matches")
def get_matches(request: Request, session_id: str, rule: str | None = None):
    session = _session(request, session_id)
    with session.lock:
        try:
            matches = session.engine.matches(rule)
        except RuleNotFoundError as e:
            _raise(e)
    return {"session_id": session_id, "count": len(matches),
            "matches": matches}


@app.post("/sessions/{session_id}/run")
def run(request: Request, session_id: str, body: RunIn | None = None):
    session = _session(request, session_id)
    with session.lock:
        try:
            result = session.engine.run(
                None if body is None else body.max_cycles)
        except CycleLimitError as e:
            _raise(e)
        emitted = [o for o in session.engine.outputs
                   if o["run_id"] == result.run_id]
    payload = result.to_dict()
    payload["session_id"] = session_id
    payload["emitted"] = emitted
    return payload


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/runs")
def list_runs(request: Request, session_id: str):
    _session(request, session_id)
    return {"session_id": session_id,
            "runs": request.app.state.store.list_runs(session_id)}


@app.get("/sessions/{session_id}/runs/{run_id}")
def get_run(request: Request, session_id: str, run_id: str):
    _session(request, session_id)
    run = request.app.state.store.get_run(run_id)
    if run is None or run["session_id"] != session_id:
        raise HTTPException(status_code=404, detail={
            "error": "run_not_found", "category": "run_not_found",
            "detail": f"run {run_id!r} not found in session {session_id!r}"})
    return run


@app.get("/sessions/{session_id}/runs/{run_id}/activations")
def get_activations(request: Request, session_id: str, run_id: str):
    _session(request, session_id)
    run = request.app.state.store.get_run(run_id)
    if run is None or run["session_id"] != session_id:
        raise HTTPException(status_code=404, detail={
            "error": "run_not_found", "category": "run_not_found",
            "detail": f"run {run_id!r} not found"})
    return {"session_id": session_id, "run_id": run_id,
            "activations": request.app.state.store.list_activations(run_id)}


@app.get("/sessions/{session_id}/fact-events")
def fact_events(request: Request, session_id: str, run_id: str | None = None):
    _session(request, session_id)
    return {"session_id": session_id,
            "events": request.app.state.store.list_fact_events(
                session_id, run_id)}
