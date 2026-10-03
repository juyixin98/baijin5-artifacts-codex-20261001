"""FastAPI validation/build interface.

Endpoints
---------
POST /v1/trees                  build a tree from a matrix or synthetic fasta
POST /v1/matrices:validate      run the declared matrix checks only
GET  /v1/runs/{run_id}          provenance record (run, steps, result)
POST /v1/runs/{run_id}/replay   re-execute and compare against provenance

Error mapping (stable contract, asserted by tests):
  input_error -> 422, state_conflict -> 409, resource_exhausted -> 413,
  computation_failure -> 500, unknown run_id -> 404.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dataclasses import asdict

from .errors import ErrorCategory, InputValidationError, NJError, UnknownRunError
from .matrix import collect_violations
from .models import DEFAULT_MAX_TAXA, BuildParams, NegativeBranchMode
from .provenance import ProvenanceStore
from .service import TreeService, _residuals_to_json

_STATUS_BY_CATEGORY = {
    ErrorCategory.INPUT_ERROR: 422,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 413,
    ErrorCategory.COMPUTATION_FAILURE: 500,
}


class MatrixPayload(BaseModel):
    labels: list[str]
    values: list[list[float]]
    max_taxa: int = DEFAULT_MAX_TAXA


class ParamsPayload(BaseModel):
    negative_branch_mode: Literal["error", "report", "clamp"] = "report"
    run_id: str | None = None
    max_taxa: int = DEFAULT_MAX_TAXA


class BuildRequest(BaseModel):
    matrix: MatrixPayload | None = None
    fasta: str | None = None
    params: ParamsPayload = ParamsPayload()


def _result_body(result) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "newick": result.newick,
        "leaf_map": result.leaf_map,
        "residuals": _residuals_to_json(result.residuals),
        "negative_branch_events": [asdict(e) for e in result.negative_events],
        "idempotent": result.idempotent,
    }


def create_app(db_path: str = ":memory:") -> FastAPI:
    service = TreeService(ProvenanceStore(db_path))
    app = FastAPI(title="njtree", version="1.0.0")
    app.state.service = service

    @app.exception_handler(NJError)
    async def nj_error_handler(_: Request, exc: NJError) -> JSONResponse:
        status = 404 if isinstance(exc, UnknownRunError) else _STATUS_BY_CATEGORY[exc.category]
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    @app.post("/v1/trees")
    def build_tree(req: BuildRequest) -> dict[str, Any]:
        if (req.matrix is None) == (req.fasta is None):
            raise InputValidationError("provide exactly one of 'matrix' or 'fasta'")
        params = BuildParams(
            negative_branch_mode=NegativeBranchMode(req.params.negative_branch_mode),
            max_taxa=req.params.max_taxa,
            run_id=req.params.run_id,
        )
        if req.matrix is not None:
            result = service.build_from_matrix(req.matrix.labels, req.matrix.values, params)
        else:
            result = service.build_from_fasta(req.fasta or "", params)
        return _result_body(result)

    @app.post("/v1/matrices:validate")
    def validate_matrix(payload: MatrixPayload) -> dict[str, Any]:
        violations = collect_violations(payload.labels, payload.values, max_taxa=payload.max_taxa)
        return {"valid": not violations, "violations": violations}

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = service.store.get_run(run_id)
        if run is None:
            raise UnknownRunError(f"unknown run_id {run_id!r}", run_id=run_id)
        return {
            "run": run,
            "steps": service.store.get_steps(run_id),
            "result": service.store.get_result(run_id),
        }

    @app.post("/v1/runs/{run_id}/replay")
    def replay_run(run_id: str) -> dict[str, Any]:
        report = service.replay(run_id)
        return {"run_id": report.run_id, "match": report.match, "differences": report.differences}

    return app
