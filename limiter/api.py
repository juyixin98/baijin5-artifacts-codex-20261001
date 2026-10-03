"""FastAPI service layer for the streaming lookahead limiter.

Endpoints:
- GET  /v1/health                    versions + algorithm contract id
- POST /v1/limit                     one-shot offline limiting (latency-compensated)
- POST /v1/sessions                  open a streaming session (fixed config)
- POST /v1/sessions/{id}/blocks      push a PCM block, receive ready frames
- POST /v1/sessions/{id}/flush       drain the delay line (final L frames)

Error contract: contract violations -> 422 with a machine-readable
``category``; unknown session -> 404; use-after-flush -> 409. No handler
swallows an unknown state into a 200.
"""

from __future__ import annotations

import uuid

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ALGORITHM_CONTRACT_VERSION, LimiterConfig
from .contract import ContractError
from .stream import StreamingLimiter, process_offline


class ConfigIn(BaseModel):
    sample_rate: int = 48000
    channels: int = 2
    threshold: float = 0.5
    lookahead_ms: float = 5.0
    attack_ms: float = 0.5
    release_ms: float = 50.0

    def to_limiter_config(self) -> LimiterConfig:
        return LimiterConfig(**self.model_dump())


class LimitRequest(BaseModel):
    config: ConfigIn = Field(default_factory=ConfigIn)
    pcm: list[list[float]]


class BlockRequest(BaseModel):
    pcm: list[list[float]]


class SessionRequest(BaseModel):
    config: ConfigIn = Field(default_factory=ConfigIn)


def create_app() -> FastAPI:
    app = FastAPI(title="lookahead-limiter", version=ALGORITHM_CONTRACT_VERSION)
    sessions: dict[str, StreamingLimiter] = {}

    @app.exception_handler(ContractError)
    async def contract_error_handler(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"category": exc.category, "detail": exc.detail}},
        )

    @app.exception_handler(ValueError)
    async def config_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"category": "bad_config", "detail": str(exc)}},
        )

    @app.get("/v1/health")
    def health() -> dict:
        import numpy
        import scipy

        return {
            "status": "ok",
            "algorithm_version": ALGORITHM_CONTRACT_VERSION,
            "peak_mode": "sample",
            "versions": {"numpy": numpy.__version__, "scipy": scipy.__version__},
        }

    @app.post("/v1/limit")
    def limit_offline(req: LimitRequest) -> dict:
        cfg = req.config.to_limiter_config()
        result = process_offline(req.pcm, cfg)
        return {
            "algorithm_version": ALGORITHM_CONTRACT_VERSION,
            "latency_samples": result.latency_samples,
            "frames": int(result.output.shape[0]),
            "channels": cfg.channels,
            "output": result.output.tolist(),
            "gain": result.gain.tolist(),
            "metrics": {
                "input_peak": result.input_peak,
                "output_peak": result.output_peak,
                "promised_ceiling": result.promised_ceiling,
                "ceiling_ok": bool(result.output_peak <= result.promised_ceiling + 1e-12),
                "gain_min": float(np.min(result.gain)) if len(result.gain) else 1.0,
            },
        }

    @app.post("/v1/sessions", status_code=201)
    def open_session(req: SessionRequest) -> dict:
        cfg = req.config.to_limiter_config()
        sid = uuid.uuid4().hex[:12]
        sessions[sid] = StreamingLimiter(cfg)
        return {
            "session_id": sid,
            "latency_samples": cfg.lookahead_samples,
            "config": cfg.to_dict(),
        }

    def _get_session(sid: str) -> StreamingLimiter:
        try:
            return sessions[sid]
        except KeyError:
            raise HTTPException(
                status_code=404, detail={"category": "unknown_session", "session_id": sid}
            ) from None

    @app.post("/v1/sessions/{sid}/blocks")
    def push_block(sid: str, req: BlockRequest) -> dict:
        lim = _get_session(sid)
        try:
            result = lim.process(req.pcm)
        except ContractError as exc:
            if exc.category == "stream_flushed":
                raise HTTPException(
                    status_code=409,
                    detail={"category": exc.category, "detail": exc.detail},
                ) from None
            raise
        return {
            "start_index": result.start_index,
            "emitted": int(result.pcm.shape[0]),
            "frames_emitted_total": lim.frames_emitted,
            "pcm": result.pcm.tolist(),
            "gain": result.gain.tolist(),
        }

    @app.post("/v1/sessions/{sid}/flush")
    def flush_session(sid: str) -> dict:
        lim = _get_session(sid)
        try:
            result = lim.flush()
        except ContractError as exc:
            raise HTTPException(
                status_code=409,
                detail={"category": exc.category, "detail": exc.detail},
            ) from None
        return {
            "start_index": result.start_index,
            "emitted": int(result.pcm.shape[0]),
            "frames_emitted_total": lim.frames_emitted,
            "pcm": result.pcm.tolist(),
            "gain": result.gain.tolist(),
        }

    return app


app = create_app()
