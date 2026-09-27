"""FastAPI 应用工厂与路由。

错误分类 -> HTTP 映射 (四类必须可区分):
  input_error           422
  state_conflict        409
  resource_exhausted    429
  computation_failed    500
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .errors import (
    ErrorCategory,
    ReasonerError,
)
from .services import ReasonerService
from .storage.database import connect, initialize
from .storage.repository import KnowledgeRepository
from .storage.runlog import RunLogger
from .api.schemas import (
    AddFactsRequest,
    CreateKBRequest,
    LoadTheoryRequest,
    QueryRequest,
)

_HTTP_STATUS = {
    ErrorCategory.INPUT_ERROR: 422,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 429,
    ErrorCategory.COMPUTATION_FAILED: 500,
}


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    conn = connect(settings.db_path)
    initialize(conn)
    repo = KnowledgeRepository(conn)
    logger = RunLogger(
        settings.request_log, settings.kernel_trace, settings.error_log
    )
    service = ReasonerService(repo, logger, settings.limits)

    app = FastAPI(
        title="受限默认规则可解释推理后端",
        version="1.0.0",
        description=(
            "严格/可撤销规则分离、无环优先关系、强否定冲突悬置、"
            "NAF 开放世界假设、支持/击败/悬置链解释。"
        ),
    )
    app.state.settings = settings
    app.state.service = service

    @app.exception_handler(ReasonerError)
    async def _handle_domain_error(_: Request, exc: ReasonerError) -> JSONResponse:
        return JSONResponse(
            status_code=_HTTP_STATUS[exc.category],
            content=exc.to_dict(),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "category": ErrorCategory.INPUT_ERROR.value,
                "error": "RequestValidationError",
                "message": "请求体不合法",
                "details": {"validation": exc.errors()},
                "run_id": None,
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "category": ErrorCategory.COMPUTATION_FAILED.value,
                "error": type(exc).__name__,
                "message": str(exc) or "未预期的内部错误",
                "details": {},
                "run_id": None,
            },
        )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "db": str(settings.db_path)}

    @app.post("/api/kbs", status_code=201)
    async def create_kb(body: CreateKBRequest) -> dict:
        return service.create_kb(body.kb_id, body.name)

    @app.get("/api/kbs")
    async def list_kbs() -> dict:
        return {"knowledge_bases": repo.list_kbs()}

    @app.delete("/api/kbs/{kb_id}", status_code=204)
    async def delete_kb(kb_id: str) -> None:
        repo.delete_kb(kb_id)

    @app.put("/api/kbs/{kb_id}/theory")
    async def load_theory(kb_id: str, body: LoadTheoryRequest) -> dict:
        return service.load_theory(kb_id, body.theory)

    @app.post("/api/kbs/{kb_id}/facts")
    async def add_facts(kb_id: str, body: AddFactsRequest) -> dict:
        return service.add_facts(kb_id, body.facts)

    @app.post("/api/kbs/{kb_id}/query")
    async def query(kb_id: str, body: QueryRequest) -> dict:
        return service.query(kb_id, body.query)

    @app.get("/api/runs")
    async def list_runs(limit: int = 50) -> dict:
        return {"runs": service.list_runs(limit)}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        run = service.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
        return run

    return app


app = create_app()
