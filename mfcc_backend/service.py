"""FastAPI entry point.

Run locally:  uvicorn mfcc_backend.service:app --port 8000

Error semantics: expected failures raise MFCCError subclasses and are
returned as structured ``{"error": {code, message, detail}}`` bodies with
a non-200 status. Anything unexpected propagates as a 500 — never a
success envelope.
"""

from __future__ import annotations

import hashlib
import platform
import uuid

import numpy as np
import scipy
import fastapi
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import MFCCConfig
from .contracts import FeaturesRequest, StreamChunkRequest, StreamStartRequest
from .errors import MFCCError, SessionNotFoundError
from .pipeline import PipelineResult, compute_pipeline
from .streaming import StreamingMFCC


def versions() -> dict:
    return {
        "mfcc_backend": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "fastapi": fastapi.__version__,
    }


def _samples_sha1(samples) -> str:
    arr = np.asarray(samples, dtype=np.float64)
    return hashlib.sha1(arr.tobytes()).hexdigest()


def _serialize_result(result: PipelineResult, request_id: str, include_intermediates: bool) -> dict:
    body = {
        "request_id": request_id,
        "meta": {
            "versions": versions(),
            "config": result.config.as_dict(),
            "n_input_samples": result.n_input_samples,
            "n_frames": result.n_frames,
            "feature_shape": list(result.mfcc.shape),
        },
        "features": {
            "mfcc": result.mfcc.tolist(),
            "delta": result.delta.tolist(),
            "delta2": result.delta2.tolist(),
        },
    }
    if include_intermediates:
        body["intermediates"] = {
            "frames": result.frames.tolist(),
            "power_spectrum": result.power.tolist(),
            "mel_energies": result.mel_energies.tolist(),
            "log_mel": result.log_mel.tolist(),
        }
    return body


def create_app() -> FastAPI:
    app = FastAPI(title="mfcc-backend", version=__version__)
    sessions: dict[str, StreamingMFCC] = {}

    @app.exception_handler(MFCCError)
    async def mfcc_error_handler(_request: Request, exc: MFCCError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.as_dict()})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "versions": versions()}

    @app.get("/v1/config/default")
    def default_config() -> dict:
        return MFCCConfig().validate().as_dict()

    @app.post("/v1/features")
    def features(req: FeaturesRequest) -> dict:
        request_id = uuid.uuid4().hex[:12]
        result = compute_pipeline(req.samples, req.to_config())
        body = _serialize_result(result, request_id, req.include_intermediates)
        body["meta"]["input_sha1"] = _samples_sha1(req.samples)
        return body

    @app.post("/v1/stream/sessions", status_code=201)
    def start_stream(req: StreamStartRequest) -> dict:
        session_id = uuid.uuid4().hex
        sessions[session_id] = StreamingMFCC(req.to_config())
        return {
            "session_id": session_id,
            "config": sessions[session_id].config.as_dict(),
        }

    def _get_session(session_id: str) -> StreamingMFCC:
        try:
            return sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(f"unknown session id: {session_id}") from None

    @app.post("/v1/stream/sessions/{session_id}/chunks")
    def push_chunk(session_id: str, req: StreamChunkRequest) -> dict:
        stream = _get_session(session_id)
        emission = stream.accept_chunk(req.samples)
        return {
            "session_id": session_id,
            "emitted": {
                "mfcc": emission.mfcc.tolist(),
                "delta": emission.delta.tolist(),
                "delta2": emission.delta2.tolist(),
            },
            "emitted_counts": emission.counts(),
            "totals": stream.totals(),
        }

    @app.post("/v1/stream/sessions/{session_id}/finish")
    def finish_stream(session_id: str) -> dict:
        stream = _get_session(session_id)
        emission = stream.finish()
        return {
            "session_id": session_id,
            "emitted": {
                "mfcc": emission.mfcc.tolist(),
                "delta": emission.delta.tolist(),
                "delta2": emission.delta2.tolist(),
            },
            "emitted_counts": emission.counts(),
            "totals": stream.totals(),
        }

    return app


app = create_app()
