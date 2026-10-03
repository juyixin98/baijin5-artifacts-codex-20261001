"""FastAPI verification interface.

Error semantics (also documented in README):

- 400 INVALID_PARAMETER   — incompatible k/window/hash/cap values
- 400 INVALID_SEQUENCE    — non-ACGT or empty sequence, malformed FASTA
- 400 SEQUENCE_TOO_SHORT  — read shorter than k + window - 1
- 404 INDEX_NOT_FOUND     — unknown index id
- 500 INDEX_STATE_ERROR   — corrupt/illegible index file
- 422                     — request body fails schema validation (FastAPI)

No handler converts an exception or unknown state into a 200.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import MinimizerConfig
from .errors import IndexNotFoundError, MinimizerIndexError
from .index import MinimizerIndex, new_index_path
from .provenance import JsonlLogger, RunInfo, environment_versions
from .sequence import SequenceRecord

_STATUS_BY_CATEGORY = {
    "INVALID_PARAMETER": 400,
    "INVALID_SEQUENCE": 400,
    "SEQUENCE_TOO_SHORT": 400,
    "INDEX_NOT_FOUND": 404,
    "INDEX_STATE_ERROR": 500,
}


class SequenceIn(BaseModel):
    name: str = Field(min_length=1)
    sequence: str = Field(min_length=1)


class BuildRequest(BaseModel):
    sequences: list[SequenceIn] = Field(min_length=1)
    config: dict | None = None


class QueryRequest(BaseModel):
    sequence: str = Field(min_length=1)
    name: str = "query"


def create_app(data_dir: str | os.PathLike = "data") -> FastAPI:
    data_dir = Path(data_dir)
    log_dir = Path(os.environ.get("MINIMIZER_LOG_DIR", "logs"))
    logger = JsonlLogger(log_dir / "service.jsonl")

    app = FastAPI(title="minimizer-seed-index", version="0.1.0")
    app.state.data_dir = data_dir

    @app.exception_handler(MinimizerIndexError)
    async def domain_error_handler(_: Request, exc: MinimizerIndexError):
        status = _STATUS_BY_CATEGORY.get(exc.category, 500)
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "category": exc.category,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            },
        )

    def load_index_or_404(index_id: str) -> MinimizerIndex:
        path = data_dir / f"index_{index_id}.db"
        if not path.exists():
            raise IndexNotFoundError(
                f"no index with id {index_id!r}",
                detail={"index_id": index_id},
            )
        return MinimizerIndex.load(path)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/meta")
    def meta() -> dict:
        return {
            "versions": environment_versions(),
            "default_config": MinimizerConfig().to_dict(),
        }

    @app.post("/indexes", status_code=201)
    def build_index(req: BuildRequest) -> dict:
        config = (
            MinimizerConfig.from_dict(req.config)
            if req.config is not None
            else MinimizerConfig()
        )
        records = [SequenceRecord(name=s.name, sequence=s.sequence) for s in req.sequences]
        index_id, path = new_index_path(data_dir)
        index, run, stats = MinimizerIndex.build(path, records, config)
        index.close()
        logger.emit(run, "build", index_id=index_id, stats=stats, steps=run.steps)
        return {
            "index_id": index_id,
            "run_id": run.run_id,
            "config": config.to_dict(),
            "stats": stats,
            "input_fingerprint": run.input_fingerprint,
        }

    @app.get("/indexes/{index_id}")
    def index_info(index_id: str) -> dict:
        index = load_index_or_404(index_id)
        try:
            run = index.read_meta("run")
            stats = index.read_meta("stats")
        finally:
            index.close()
        return {
            "index_id": index_id,
            "run_id": run.get("run_id"),
            "config": json.loads(index.config.fingerprint()),
            "stats": stats,
            "build_versions": run.get("versions"),
        }

    @app.post("/indexes/{index_id}/queries")
    def query_index(index_id: str, req: QueryRequest) -> dict:
        index = load_index_or_404(index_id)
        try:
            run = RunInfo(
                kind="query", config_fingerprint=index.config.fingerprint()
            )
            read = SequenceRecord(name=req.name, sequence=req.sequence)
            result = index.query(read, run=run)
        finally:
            index.close()
        logger.emit(
            run,
            "query",
            index_id=index_id,
            query=req.name,
            steps=run.steps,
            candidates=len(result.candidates),
        )
        return {
            "index_id": index_id,
            "run_id": run.run_id,
            **result.to_dict(),
        }

    return app


#: Service entry point: ``uvicorn app.service:app`` (uses ./data, ./logs).
app = create_app(os.environ.get("MINIMIZER_DATA_DIR", "data"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
