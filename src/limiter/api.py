"""FastAPI service exposing the lookahead limiter.

Endpoints:
    GET  /health     -- liveness + library versions
    POST /v1/limit   -- process one PCM signal (channel-major JSON arrays)

All errors are explicit: invalid input -> 4xx with a reason; unexpected
failures -> 500 with the run id. Nothing is silently coerced to success.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import LimiterConfig
from .offline import limit_offline
from .runlog import RunLog, library_versions, pcm_sha256

logger = logging.getLogger("limiter.api")

app = FastAPI(title="lookahead-limiter", version="0.1.0")


class LimitRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    channels: list[list[float]] = Field(..., description="channel-major PCM")
    block_size: int | None = Field(default=None, gt=0)
    run_id: str | None = None


class LimitResponse(BaseModel):
    run_id: str
    latency_samples: int
    output: list[list[float]]  # channel-major, delay-compensated
    gain: list[float]
    stats: dict[str, Any]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "versions": library_versions()}


@app.post("/v1/limit", response_model=LimitResponse)
def limit(req: LimitRequest) -> LimitResponse:
    run_log = RunLog(run_id=req.run_id)
    try:
        config = LimiterConfig.from_dict(req.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not req.channels:
        raise HTTPException(status_code=400, detail="channels must be non-empty")
    lengths = {len(ch) for ch in req.channels}
    if len(lengths) != 1:
        raise HTTPException(
            status_code=400, detail=f"ragged channels: lengths {sorted(lengths)}"
        )
    pcm = np.asarray(req.channels, dtype=np.float64).T  # (n_samples, n_channels)
    if pcm.shape[0] == 0:
        raise HTTPException(status_code=400, detail="signal must be non-empty")
    if not np.all(np.isfinite(pcm)):
        raise HTTPException(status_code=400, detail="signal contains non-finite values")

    run_log.event(
        "api_request",
        n_samples=int(pcm.shape[0]),
        num_channels=int(pcm.shape[1]),
        input_sha256=pcm_sha256(pcm),
    )
    result = limit_offline(config, pcm, block_size=req.block_size, run_log=run_log)
    return LimitResponse(
        run_id=run_log.run_id,
        latency_samples=result.latency_samples,
        output=result.output.T.tolist(),
        gain=result.gain.tolist(),
        stats=result.stats,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"internal error: {type(exc).__name__}"},
    )
