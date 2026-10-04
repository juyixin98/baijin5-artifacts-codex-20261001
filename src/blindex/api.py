"""FastAPI 接口层。

- 每个请求关联 request_id（可经 X-Request-Id 传入，否则生成），
  贯穿响应体、响应头与审计日志。
- 查询结果展示关键步骤：搜索了哪些索引版本、候选数、二次确认排除数、
  不确定结论（解密失败记录及类别）单列。
- 错误响应统一携带失败类别 category。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .audit import AuditLog
from .errors import BlindIndexError
from .service import BlindIndexService
from .storage import Storage
from .verify import IndependentVerifier


class CreateRecordRequest(BaseModel):
    fields: dict


class QueryRequest(BaseModel):
    field: str
    value: str | None = None


class ReindexRequest(BaseModel):
    limit: int = 100


class FixturesRequest(BaseModel):
    fixtures: dict[str, dict]


class VerifyCheckRequest(BaseModel):
    field: str
    value: str


def create_app(
    service: BlindIndexService,
    storage: Storage,
    audit: AuditLog,
    verifier: IndependentVerifier,
) -> FastAPI:
    app = FastAPI(title="blindex", version="0.1.0")
    fixtures_store: dict[str, dict] = {}

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(BlindIndexError)
    async def blind_index_error_handler(request: Request, exc: BlindIndexError):
        body = exc.to_dict()
        body["request_id"] = getattr(request.state, "request_id", None)
        status = 404 if exc.category.value == "NOT_FOUND" else 400
        return JSONResponse(status_code=status, content=body)

    def rid(request: Request) -> str:
        return request.state.request_id

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/records", status_code=201)
    def create_record(req: CreateRecordRequest, request: Request):
        record_id = service.create_record(req.fields, rid(request))
        return {"record_id": record_id, "request_id": rid(request)}

    @app.get("/records/{record_id}")
    def get_record(record_id: str, request: Request):
        out = service.get_record(record_id, rid(request))
        out["request_id"] = rid(request)
        return out

    @app.post("/query")
    def query(req: QueryRequest, request: Request):
        result = service.query(req.field, req.value, rid(request))
        result["request_id"] = rid(request)
        return result

    @app.post("/admin/rotate-index-key")
    def rotate(request: Request):
        out = service.start_index_rotation(rid(request))
        out["request_id"] = rid(request)
        return out

    @app.post("/admin/reindex")
    def reindex(req: ReindexRequest, request: Request):
        out = service.reindex_batch(req.limit, rid(request))
        out["request_id"] = rid(request)
        return out

    @app.get("/admin/rotation-status")
    def rotation_status():
        return service.rotation_status()

    @app.get("/audit")
    def list_audit():
        return {"entries": storage.list_audit()}

    # ---- 独立验证端点 -------------------------------------------------------
    @app.put("/verify/fixtures")
    def put_fixtures(req: FixturesRequest, request: Request):
        fixtures_store.clear()
        fixtures_store.update(req.fixtures)
        audit.log("fixtures", rid(request), count=len(fixtures_store))
        return {"loaded": len(fixtures_store), "request_id": rid(request)}

    @app.post("/verify/check")
    def verify_check(req: VerifyCheckRequest, request: Request):
        actual = service.query(req.field, req.value, rid(request))
        report = verifier.check_query(
            fixtures_store, req.field, req.value, actual["matched_record_ids"]
        )
        report["request_id"] = rid(request)
        return report

    return app
