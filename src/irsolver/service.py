"""FastAPI 服务接口：POST /solve 与 GET /health。

请求/响应均为 JSON。矩阵元素建议以字符串形式提供（精确十进制），
也接受 JSON 数值。响应包含逐列状态、误差证据、条件数说明与决策日志；
非有限浮点值统一序列化为 null 并在 message 中说明。
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config.settings import SolverSettings, load_settings

from . import __version__
from .diagnostics import get_logger, new_request_id
from .inputs import InputValidationError, parse_system
from .refinement import ColumnReport, SolveReport, solve_system


class SolveRequestModel(BaseModel):
    """求解请求。a 为 n×n 系数矩阵，b 为 n×m 右端（每列独立求解）。

    矩阵元素接受 JSON 数值或十进制字符串；权威校验由 inputs 模块执行，
    以便返回统一的结构化错误类别（ragged / non_finite / not_square 等）。
    """

    a: list[list[Any]]
    b: list[list[Any]]
    tolerance: float | None = Field(default=None, gt=0.0, lt=1.0)
    sensitive: bool = False
    include_solution_text: bool = False


def _sanitize(obj: Any) -> Any:
    """递归清理非有限浮点值（JSON 无法表示 inf/nan），替换为 null。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _column_json(col: ColumnReport, include_text: bool) -> dict:
    out: dict = {
        "index": col.index,
        "status": col.status,
        "tier_used": col.tier_used,
        "iterations": col.iterations,
        "eta_componentwise": col.eta_componentwise,
        "eta_normwise": col.eta_normwise,
        "forward_error_bound": col.forward_error_bound,
        "message": col.message,
        "solution": col.solution,
        "trace": [
            {
                "tier": r.tier,
                "iteration": r.iteration,
                "eta_componentwise": r.eta_componentwise,
                "eta_normwise": r.eta_normwise,
                "step_norm": r.step_norm,
            }
            for r in col.trace
        ],
    }
    if include_text and col.solution is not None:
        out["solution_text"] = [repr(v) for v in col.solution]
    return out


def _report_json(report: SolveReport, include_text: bool, sensitive: bool) -> dict:
    columns = [_column_json(c, include_text) for c in report.columns]
    solution = None
    if all(c.solution is not None for c in report.columns):
        n = report.matrix_summary["shape_a"][0]
        solution = [
            [report.columns[j].solution[i] for j in range(len(report.columns))]  # type: ignore[index]
            for i in range(n)
        ]
    return _sanitize(
        {
            "request_id": report.request_id,
            "status": report.status,
            "condition": None
            if sensitive
            else {
                "kappa": report.condition.kappa,
                "method": report.condition.method,
                "rank": report.condition.rank,
                "rank_deficient": report.condition.rank_deficient,
            },
            "accuracy_note": None if sensitive else report.accuracy_note,
            "matrix_summary": report.matrix_summary,
            "columns": columns,
            "solution": solution,
            "diagnostics": {"journal": report.journal},
        }
    )


def create_app(settings: SolverSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="irsolver",
        version=__version__,
        description="低精度分解 + 高精度残差的迭代精化线性求解服务",
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # noqa: ANN001 - FastAPI 兜底处理器
        request_id = new_request_id()
        get_logger(request_id).exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"request_id": request_id, "error": {"type": "internal"}},
        )

    @app.post("/solve")
    def solve(payload: SolveRequestModel) -> JSONResponse:
        request_id = new_request_id()
        log = get_logger(request_id)
        try:
            A, B = parse_system(payload.a, payload.b)
        except InputValidationError as exc:
            log.warning("rejected %s", exc)
            return JSONResponse(
                status_code=422,
                content={
                    "request_id": request_id,
                    "error": {
                        "type": "invalid_input",
                        "reason": exc.reason,
                        "detail": exc.detail,
                    },
                },
            )
        effective = (
            settings
            if payload.tolerance is None
            else replace(settings, tolerance=payload.tolerance)
        )
        report = solve_system(
            A, B, effective, request_id=request_id, sensitive=payload.sensitive
        )
        return JSONResponse(
            content=_report_json(report, payload.include_solution_text, payload.sensitive)
        )

    return app


app = create_app()
