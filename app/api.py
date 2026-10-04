"""接口层:FastAPI 路由。统一响应信封 {ok, data, error},失败必带类别。"""
from __future__ import annotations

import platform
from typing import Optional

import cryptography
import fastapi
import phe
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .errors import STATUS_MAP, ErrorCategory, ServiceError
from .service import AggregationService


class CreateBatchRequest(BaseModel):
    label: Optional[str] = None
    sum_bound: Optional[str] = None  # 大整数用十进制字符串


class SubmissionRequest(BaseModel):
    participant_id: str
    ciphertext: str      # 十进制字符串
    exponent: int = 0
    weight: str          # 十进制字符串
    key_fingerprint: str
    declared_abs: str    # 参与者声明的 |x|,十进制字符串


class ReferenceEntry(BaseModel):
    participant_id: str
    value: str


class VerifyRequest(BaseModel):
    reference: list[ReferenceEntry]


def _ok(data) -> dict:
    return {"ok": True, "data": data, "error": None}


def _parse_int(raw: str, field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            ErrorCategory.VALIDATION, f"字段 {field} 不是合法整数: {raw!r}"
        ) from exc


def create_app(settings: Settings,
               service: Optional[AggregationService] = None) -> FastAPI:
    svc = service or AggregationService(settings)
    app = FastAPI(title="paillier-agg-local", version=__version__)

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError):
        status = STATUS_MAP.get(exc.category, 500)
        return JSONResponse(status_code=status,
                            content={"ok": False, "data": None,
                                     "error": exc.to_dict()})

    @app.get("/health")
    def health():
        return _ok({
            "service_version": __version__,
            "run_id": svc.run_id,
            "python": platform.python_version(),
            "phe": phe.__version__,
            "fastapi": fastapi.__version__,
            "cryptography": cryptography.__version__,
        })

    @app.post("/batches", status_code=201)
    def create_batch(req: CreateBatchRequest):
        bound = _parse_int(req.sum_bound, "sum_bound") if req.sum_bound else None
        return _ok(svc.create_batch(label=req.label, sum_bound=bound))

    @app.get("/batches/{batch_id}")
    def get_batch(batch_id: str):
        return _ok(svc.get_batch(batch_id))

    @app.post("/batches/{batch_id}/submissions", status_code=201)
    def submit(batch_id: str, req: SubmissionRequest):
        return _ok(svc.submit(
            batch_id=batch_id,
            participant_id=req.participant_id,
            c=_parse_int(req.ciphertext, "ciphertext"),
            exponent=req.exponent,
            weight=_parse_int(req.weight, "weight"),
            key_fingerprint=req.key_fingerprint,
            declared_abs=_parse_int(req.declared_abs, "declared_abs"),
        ))

    @app.post("/batches/{batch_id}/aggregate")
    def aggregate(batch_id: str):
        return _ok(svc.aggregate(batch_id))

    @app.post("/batches/{batch_id}/decrypt")
    def decrypt(batch_id: str):
        return _ok(svc.decrypt(batch_id))

    @app.post("/batches/{batch_id}/verify")
    def verify(batch_id: str, req: VerifyRequest):
        reference = [(e.participant_id, _parse_int(e.value, "value"))
                     for e in req.reference]
        return _ok(svc.verify(batch_id, reference))

    @app.post("/batches/{batch_id}/multiply-ciphertexts")
    def multiply_ciphertexts(batch_id: str):
        # 明确拒绝:Paillier 仅加法同态,不存在密文*密文运算
        raise ServiceError(
            ErrorCategory.UNSUPPORTED_OPERATION,
            "Paillier 是加法同态方案:支持密文加法与密文*明文标量,"
            "不支持密文*密文。本服务不声称通用密文计算。",
        )

    @app.get("/batches/{batch_id}/audit")
    def audit(batch_id: str):
        return _ok(svc.list_audit(batch_id))

    return app
