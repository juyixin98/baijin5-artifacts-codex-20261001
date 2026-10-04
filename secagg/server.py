"""FastAPI 服务器:HTTP 适配层,把协议错误分类映射为可区分的响应.

错误类别 -> HTTP 状态码:
    input_error         -> 400
    state_conflict      -> 409
    resource_exhausted  -> 413
    computation_failure -> 422
响应体统一为 {category, code, message, detail},客户端与测试据此区分
输入错误、状态冲突、资源耗尽与计算失败。
"""

from __future__ import annotations

import base64

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .errors import ErrorCategory, SecAggError
from .protocol import SecAggServer
from .state import StateStore

STATUS_BY_CATEGORY = {
    ErrorCategory.INPUT_ERROR: 400,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.RESOURCE_EXHAUSTED: 413,
    ErrorCategory.COMPUTATION_FAILURE: 422,
}


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class CreateRunRequest(BaseModel):
    client_ids: list[str]
    threshold: int = Field(ge=2)
    vector_len: int = Field(ge=1)
    scale: int = 1 << 20
    elem_bound: int = 1 << 40
    max_vector_len: int = 1 << 16


class KeysRequest(BaseModel):
    client_id: str
    c_pk: str
    s_pk: str


class SharesRequest(BaseModel):
    client_id: str
    ciphertexts: dict[str, str]


class MaskedRequest(BaseModel):
    client_id: str
    vector: list[int]


class RecoveryRequest(BaseModel):
    client_id: str
    b_shares: dict[str, list]
    sk_shares: dict[str, list]


def create_app(store: StateStore | None = None) -> FastAPI:
    server = SecAggServer(store or StateStore(":memory:"))
    app = FastAPI(title="secagg-teach", version="0.1.0")
    app.state.server = server

    @app.exception_handler(SecAggError)
    async def secagg_error_handler(_: Request, exc: SecAggError) -> JSONResponse:
        return JSONResponse(
            status_code=STATUS_BY_CATEGORY[exc.category],
            content=exc.to_dict(),
        )

    @app.post("/runs")
    def create_run(req: CreateRunRequest) -> dict:
        run_id = server.create_run(
            client_ids=req.client_ids,
            threshold=req.threshold,
            vector_len=req.vector_len,
            scale=req.scale,
            elem_bound=req.elem_bound,
            max_vector_len=req.max_vector_len,
        )
        return {"run_id": run_id}

    @app.post("/runs/{run_id}/keys")
    def submit_keys(run_id: str, req: KeysRequest) -> dict:
        return server.submit_keys(run_id, req.client_id,
                                  _b64d(req.c_pk), _b64d(req.s_pk))

    @app.get("/runs/{run_id}/public_keys")
    def public_keys(run_id: str) -> dict:
        return server.get_public_keys(run_id)

    @app.post("/runs/{run_id}/shares")
    def submit_shares(run_id: str, req: SharesRequest) -> dict:
        return server.submit_shares(run_id, req.client_id, req.ciphertexts)

    @app.get("/runs/{run_id}/shares/{client_id}")
    def shares_for(run_id: str, client_id: str) -> dict:
        return server.get_shares_for(run_id, client_id)

    @app.post("/runs/{run_id}/masked")
    def submit_masked(run_id: str, req: MaskedRequest) -> dict:
        return server.submit_masked_input(run_id, req.client_id, req.vector)

    @app.post("/runs/{run_id}/advance")
    def advance(run_id: str) -> dict:
        return server.advance(run_id)

    @app.get("/runs/{run_id}/active_set")
    def active_set(run_id: str) -> dict:
        return server.get_active_set(run_id)

    @app.post("/runs/{run_id}/recovery")
    def recovery(run_id: str, req: RecoveryRequest) -> dict:
        return server.submit_recovery(
            run_id, req.client_id, req.b_shares, req.sk_shares)

    @app.get("/runs/{run_id}/result")
    def result(run_id: str) -> dict:
        return server.get_result(run_id)

    @app.get("/runs/{run_id}/audit")
    def audit(run_id: str) -> list[dict]:
        return server.store.get_audit(run_id)

    return app
