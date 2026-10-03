"""FastAPI surface: estimate, tiled estimate, fixtures, validation.

Every response carries the request id (also in the X-Request-ID header);
failures and uncertain conclusions are separate fields, never merged into
a generic message. Logs emitted during a request carry the same id.
"""

from __future__ import annotations

import platform
import time
import uuid
from dataclasses import replace

import numpy as np
import PIL
import scipy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.fixtures import load_fixtures
from app.imaging import ImageContractError, decode_png_b64, encode_png_b64, validate_pair
from app.jobs import run_tiled_job
from app.kernel.pipeline import estimate_shift
from app.logging_setup import get_logger, request_id_var
from app.schemas import (
    EstimateRequest,
    EstimateResponse,
    FixtureDetail,
    FixtureInfo,
    HealthResponse,
    PeakModel,
    ShiftModel,
    TileResultModel,
    TiledJobResponse,
    ValidationResponse,
)
from app.validation import run_validation
from app.version import __version__

log = get_logger("api")
settings = get_settings()

app = FastAPI(title="opp495-b translation estimation", version=__version__)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1e3
        log.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(ImageContractError)
async def contract_error_handler(request: Request, exc: ImageContractError):
    log.warning("contract violation: %s", exc)
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "request_id": request_id_var.get()},
    )


def _versions() -> dict[str, str]:
    return {
        "app": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
    }


def _to_response(result, request_id: str) -> EstimateResponse:
    return EstimateResponse(
        request_id=request_id,
        status=result.status,
        shift=ShiftModel(dy=result.shift[0], dx=result.shift[1]) if result.shift else None,
        integer_shift=(
            ShiftModel(dy=float(result.integer_shift[0]), dx=float(result.integer_shift[1]))
            if result.integer_shift
            else None
        ),
        subpixel_offset=(
            ShiftModel(dy=result.subpixel_offset[0], dx=result.subpixel_offset[1])
            if result.subpixel_offset
            else None
        ),
        confidence=result.confidence,
        psr=result.psr,
        second_peak_ratio=result.second_peak_ratio,
        overlap_fraction=result.overlap_fraction,
        peaks=[PeakModel(dy=p.dy, dx=p.dx, value=p.value) for p in result.peaks],
        ambiguity_peaks=[
            PeakModel(dy=p.dy, dx=p.dx, value=p.value) for p in result.ambiguity_peaks
        ],
        failures=result.failures,
        uncertainties=result.uncertainties,
        diagnostics={**result.diagnostics, "versions": _versions()},
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", versions=_versions())


@app.post("/v1/estimate", response_model=EstimateResponse)
def estimate(req: EstimateRequest, request: Request) -> EstimateResponse:
    request_id = request_id_var.get()
    cfg = replace(settings.kernel, window=req.window) if req.window else settings.kernel
    img_a = decode_png_b64(req.image_a, "image_a", settings)
    img_b = decode_png_b64(req.image_b, "image_b", settings)
    validate_pair(img_a, img_b)
    log.info("estimate: shape=%s window=%s", img_a.shape, cfg.window)
    result = estimate_shift(img_a, img_b, cfg)
    log.info(
        "estimate: status=%s shift=%s confidence=%s failures=%s uncertainties=%s",
        result.status,
        result.shift,
        result.confidence,
        result.failures,
        result.uncertainties,
    )
    return _to_response(result, request_id)


@app.post("/v1/estimate/tiled", response_model=TiledJobResponse)
def estimate_tiled(req: EstimateRequest) -> TiledJobResponse:
    request_id = request_id_var.get()
    cfg = replace(settings.kernel, window=req.window) if req.window else settings.kernel
    img_a = decode_png_b64(req.image_a, "image_a", settings)
    img_b = decode_png_b64(req.image_b, "image_b", settings)
    validate_pair(img_a, img_b)
    log.info("tiled estimate: shape=%s tile=%s halo=%s", img_a.shape, settings.tile_size, settings.tile_halo)
    result = run_tiled_job(
        img_a, img_b, cfg, tile_size=settings.tile_size, halo=settings.tile_halo
    )
    log.info(
        "tiled estimate: status=%s global=%s used=%d/%d",
        result.status,
        result.global_shift,
        result.tiles_used,
        len(result.tiles),
    )
    return TiledJobResponse(
        request_id=request_id,
        status=result.status,
        global_shift=(
            ShiftModel(dy=result.global_shift[0], dx=result.global_shift[1])
            if result.global_shift
            else None
        ),
        spread_px=result.spread_px,
        tiles_total=len(result.tiles),
        tiles_used=result.tiles_used,
        tiles=[
            TileResultModel(
                origin=t.origin,
                status=t.estimate.status,
                shift=(
                    ShiftModel(dy=t.estimate.shift[0], dx=t.estimate.shift[1])
                    if t.estimate.shift
                    else None
                ),
                confidence=t.estimate.confidence,
                failures=t.estimate.failures,
                uncertainties=t.estimate.uncertainties,
            )
            for t in result.tiles
        ],
        failures=result.failures,
        uncertainties=result.uncertainties,
    )


@app.get("/v1/fixtures", response_model=list[FixtureInfo])
def list_fixtures() -> list[FixtureInfo]:
    return [
        FixtureInfo(
            name=fx.name,
            category=fx.category,
            ground_truth_shift=(
                ShiftModel(dy=fx.ground_truth_shift[0], dx=fx.ground_truth_shift[1])
                if fx.ground_truth_shift
                else None
            ),
            expected_status=fx.expected_status,
            note=fx.note,
        )
        for fx in load_fixtures(settings.fixtures_dir)
    ]


@app.get("/v1/fixtures/{name}", response_model=FixtureDetail)
def get_fixture(name: str) -> FixtureDetail:
    for fx in load_fixtures(settings.fixtures_dir):
        if fx.name == name:
            return FixtureDetail(
                name=fx.name,
                category=fx.category,
                ground_truth_shift=(
                    ShiftModel(dy=fx.ground_truth_shift[0], dx=fx.ground_truth_shift[1])
                    if fx.ground_truth_shift
                    else None
                ),
                expected_status=fx.expected_status,
                note=fx.note,
                image_a=encode_png_b64(fx.img_a),
                image_b=encode_png_b64(fx.img_b),
            )
    raise ImageContractError(f"unknown fixture: {name!r}")


@app.post("/v1/validation/run", response_model=ValidationResponse)
def validation_run() -> ValidationResponse:
    request_id = request_id_var.get()
    log.info("validation run started")
    report = run_validation()
    log.info("validation run finished: %s", report["summary"])
    return ValidationResponse(request_id=request_id, report=report)
