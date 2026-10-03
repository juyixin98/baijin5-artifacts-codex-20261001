"""FastAPI validation interface.

Thin HTTP layer over service.run_distance. Error categories map to distinct
HTTP statuses so clients can distinguish failure classes:

    input_error         -> 400
    not_found           -> 404
    state_conflict      -> 409
    resource_exhausted  -> 413
    computation_failure -> 422
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .errors import ErrorCategory, NotFoundError, SeqDistError
from .models import MODELS
from .parsing import parse_fasta_pair
from .provenance import connect, get_run
from .service import run_distance

_STATUS_BY_CATEGORY = {
    ErrorCategory.INPUT_ERROR: 400,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 413,
    ErrorCategory.COMPUTATION_FAILURE: 422,
}

DEFAULT_DB_PATH = os.environ.get("SEQDIST_DB", "seqdist_runs.sqlite3")


class DistanceRequest(BaseModel):
    seq1: str | None = Field(default=None, description="Aligned sequence 1 (raw)")
    seq2: str | None = Field(default=None, description="Aligned sequence 2 (raw)")
    fasta: str | None = Field(
        default=None, description="FASTA text with exactly two aligned records"
    )
    model: str = Field(default="jc69", description="p | jc69 | k80")
    n_replicates: int = Field(default=1000, ge=0)
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    seed: int = Field(default=0, ge=0)


def _resolve_pair(req: DistanceRequest) -> tuple[str, str]:
    from .errors import InputValidationError

    if req.fasta is not None:
        pair = parse_fasta_pair(req.fasta)
        return pair.seq1, pair.seq2
    if req.seq1 is None or req.seq2 is None:
        raise InputValidationError(
            "provide either fasta or both seq1 and seq2",
            detail={"got_seq1": req.seq1 is not None, "got_seq2": req.seq2 is not None},
        )
    return req.seq1, req.seq2


def create_app(db_path: str = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="seqdist", version="0.1.0")
    conn = connect(db_path)

    def get_conn() -> sqlite3.Connection:
        return conn

    @app.exception_handler(SeqDistError)
    async def seqdist_error_handler(_: Request, exc: SeqDistError) -> JSONResponse:
        status = _STATUS_BY_CATEGORY[exc.category]
        return JSONResponse(status_code=status, content=exc.to_dict())

    @app.post("/v1/distance")
    def post_distance(
        req: DistanceRequest, db: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        seq1, seq2 = _resolve_pair(req)
        return run_distance(
            db,
            seq1=seq1,
            seq2=seq2,
            model=req.model,
            n_replicates=req.n_replicates,
            alpha=req.alpha,
            seed=req.seed,
        )

    @app.get("/v1/runs/{run_id}")
    def get_run_record(
        run_id: str, db: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        record = get_run(db, run_id)
        if record is None:
            raise NotFoundError("run id not found", detail={"run_id": run_id})
        return record

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {
            name: {
                "description": spec.description,
                "assumptions": list(spec.assumptions),
                "valid_domain": spec.valid_domain,
            }
            for name, spec in MODELS.items()
        }

    return app


app = create_app()
