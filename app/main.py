"""FastAPI surface: thinning, skeleton graph, and topology validation."""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.config import load_settings
from app.contracts import (
    ERROR_CONTRACT_VIOLATION,
    ERROR_ENGINE,
    ERROR_UNKNOWN_SAMPLE,
    ThinRequest,
    image_to_payload,
    validate_binary_image,
)
from app.graph import skeleton_graph
from app.kernel import thin
from app.logging_utils import configure_logging, current_request_id, get_logger, set_request_id
from app.samples import SAMPLES, load_sample
from app.tiling import thin_tiled
from app.validation import topology_report

settings = load_settings()
configure_logging()
log = get_logger("api")

app = FastAPI(title="skeleton-backend", version=__version__)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = set_request_id(request.headers.get("x-request-id"))
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


def _error(status: int, category: str, detail: str) -> JSONResponse:
    log.warning("request failed category=%s detail=%s", category, detail)
    return JSONResponse(
        status_code=status,
        content={
            "error": {"category": category, "detail": detail},
            "request_id": current_request_id(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return _error(422, ERROR_CONTRACT_VIOLATION, str(exc.errors()))


@app.exception_handler(Exception)
async def engine_handler(request: Request, exc: Exception):
    log.exception("unhandled engine error")
    return _error(500, ERROR_ENGINE, f"{type(exc).__name__}: {exc}")


def _resolve_image(req: ThinRequest) -> np.ndarray | JSONResponse:
    if req.sample is not None:
        try:
            return load_sample(req.sample)
        except KeyError as exc:
            return _error(422, ERROR_UNKNOWN_SAMPLE, str(exc))
    try:
        return validate_binary_image(req.pixels or [], settings.max_image_dim)
    except ValueError as exc:
        return _error(422, ERROR_CONTRACT_VIOLATION, str(exc))


def _run_thinning(req: ThinRequest, image: np.ndarray):
    """Dispatch to the requested engine; returns a result with shared fields."""
    max_rounds = req.max_rounds or settings.max_rounds
    if req.engine == "tiled":
        tile_size = req.tile_size or settings.default_tile_size
        result = thin_tiled(image, tile_size=tile_size, max_rounds=max_rounds,
                            halo_width=settings.halo_width)
        extra = {"tile_size": result.tile_size, "tile_count": result.tile_count,
                 "halo_width": result.halo_width}
    else:
        result = thin(image, max_rounds=max_rounds)
        extra = {}
    log.info(
        "thinning done engine=%s shape=%s rounds=%d deleted=%d converged=%s",
        req.engine, image.shape, result.rounds,
        sum(a + b for a, b in result.deletions_per_round), result.converged,
    )
    return result, extra


def _trace_payload(result, engine: str, extra: dict) -> dict:
    return {
        "request_id": current_request_id(),
        "version": __version__,
        "engine": engine,
        "rounds": result.rounds,
        "deletions_per_round": [list(p) for p in result.deletions_per_round],
        "converged": result.converged,
        **extra,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/v1/samples")
def list_samples() -> dict:
    return {"samples": sorted(SAMPLES), "request_id": current_request_id()}


@app.post("/v1/thin")
def thin_endpoint(req: ThinRequest):
    image = _resolve_image(req)
    if isinstance(image, JSONResponse):
        return image
    log.info("thin request engine=%s shape=%s", req.engine, image.shape)
    result, extra = _run_thinning(req, image)
    return {
        **_trace_payload(result, req.engine, extra),
        "shape": list(image.shape),
        "skeleton": image_to_payload(result.skeleton),
    }


@app.post("/v1/graph")
def graph_endpoint(req: ThinRequest):
    image = _resolve_image(req)
    if isinstance(image, JSONResponse):
        return image
    result, extra = _run_thinning(req, image)
    graph = skeleton_graph(result.skeleton)
    summary = graph.to_dict()["summary"]
    log.info("graph mapped %s", summary)
    return {
        **_trace_payload(result, req.engine, extra),
        "shape": list(image.shape),
        "skeleton": image_to_payload(result.skeleton),
        "graph": graph.to_dict(),
    }


@app.post("/v1/validate")
def validate_endpoint(req: ThinRequest):
    image = _resolve_image(req)
    if isinstance(image, JSONResponse):
        return image
    result, extra = _run_thinning(req, image)
    report = topology_report(image, result.skeleton, converged=result.converged)
    if report.failures:
        log.error("topology failures: %s", list(report.failures))
    if report.uncertain:
        log.warning("uncertain conclusions: %s", list(report.uncertain))
    return {
        **_trace_payload(result, req.engine, extra),
        "shape": list(image.shape),
        "report": report.to_dict(),
    }
