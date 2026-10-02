"""FastAPI service: translation estimation, validation and diagnostics."""
from __future__ import annotations

import base64
import io
import logging
import uuid

import numpy as np
import scipy
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image

from . import __version__, fixtures_io, jobs
from .config import DEFAULT_CONFIG, KernelConfig
from .contracts import (EstimateRequest, TiledEstimateRequest, ValidateRequest)
from .kernel import KERNEL_VERSION, estimate_translation
from .logging_utils import configure_logging, request_id_var

configure_logging()
log = logging.getLogger("opp495.api")

app = FastAPI(title="opp495 translation-estimation backend", version=__version__)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response
    finally:
        request_id_var.reset(token)


def _versions() -> dict:
    return {
        "app": __version__,
        "kernel": KERNEL_VERSION,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def _decode_png(png_base64: str) -> np.ndarray:
    try:
        raw = base64.b64decode(png_base64, validate=True)
        img = Image.open(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"invalid base64 PNG payload: {exc}") from exc
    if img.mode not in ("L", "I;16", "I", "F"):
        img = img.convert("L")  # declared: RGB input is reduced to luminance
    arr = np.asarray(img, dtype=np.float64)
    if arr.ndim != 2 or not np.all(np.isfinite(arr)):
        raise HTTPException(status_code=400, detail="decoded image is not finite 2-D grayscale")
    return arr


def _resolve_image(payload) -> np.ndarray:
    if payload.png_base64:
        return _decode_png(payload.png_base64)
    try:
        ref, mov, _ = fixtures_io.load_fixture_pair(payload.fixture_id)
    except fixtures_io.FixtureNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown fixture: {exc}") from exc
    return ref if payload.role == "reference" else mov


def _config_from(overrides) -> KernelConfig:
    if overrides is None:
        return DEFAULT_CONFIG
    return DEFAULT_CONFIG.with_overrides(**overrides.model_dump())


@app.get("/v1/health")
def health():
    return {"status": "ok"}


@app.get("/v1/version")
def version():
    return _versions()


@app.get("/v1/fixtures")
def list_fixtures():
    try:
        return {"fixtures": fixtures_io.list_fixtures()}
    except fixtures_io.FixtureNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/estimate")
def estimate(req: EstimateRequest, request: Request):
    rid = req.request_id or request.headers.get("x-request-id") or request_id_var.get()
    log.info("estimate start")
    ref = _resolve_image(req.reference)
    mov = _resolve_image(req.moving)
    config = _config_from(req.config)
    result = estimate_translation(ref, mov, config)
    body = result.to_dict()
    body["request_id"] = rid
    body["versions"] = _versions()
    log.info("estimate done status=%s failure=%s uncertainties=%s",
             body["status"], body["failure_reason"], body["uncertainties"])
    return JSONResponse(body)


@app.post("/v1/estimate/tiled")
def estimate_tiled(req: TiledEstimateRequest, request: Request):
    rid = req.request_id or request.headers.get("x-request-id") or request_id_var.get()
    log.info("tiled estimate start tile=%d stride=%d", req.tile_size, req.stride)
    ref = _resolve_image(req.reference)
    mov = _resolve_image(req.moving)
    if ref.shape != mov.shape:
        raise HTTPException(status_code=400, detail="shape mismatch between reference and moving")
    body = jobs.estimate_tiled(ref, mov, tile_size=req.tile_size, stride=req.stride)
    body["request_id"] = rid
    body["versions"] = _versions()
    log.info("tiled estimate done status=%s", body["status"])
    return JSONResponse(body)


def _validate_one(fixture_id: str, tolerance_px: float) -> dict:
    ref, mov, entry = fixtures_io.load_fixture_pair(fixture_id)
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    body = result.to_dict()
    report = {
        "fixture_id": fixture_id,
        "expected_category": entry.get("expected_category"),
        "observed_status": body["status"],
        "failure_reason": body["failure_reason"],
        "uncertainties": body["uncertainties"],
        "category_match": body["status"] == entry.get("expected_category")
                          or entry.get("expected_category") in body["uncertainties"]
                          or body["failure_reason"] == entry.get("expected_category"),
    }
    gt = entry.get("ground_truth_shift")
    if gt is not None and body["shift"] is not None:
        err = float(np.hypot(body["shift"]["dy"] - gt[0], body["shift"]["dx"] - gt[1]))
        report["ground_truth_shift"] = {"dy": gt[0], "dx": gt[1]}
        report["estimated_shift"] = body["shift"]
        report["localization_error_px"] = err
        report["within_tolerance"] = bool(err <= tolerance_px)
    return report


@app.post("/v1/validate")
def validate(req: ValidateRequest, request: Request):
    rid = request.headers.get("x-request-id") or request_id_var.get()
    log.info("validate start fixture=%s", req.fixture_id or "ALL")
    try:
        if req.fixture_id:
            reports = [_validate_one(req.fixture_id, req.tolerance_px)]
        else:
            reports = [_validate_one(e["id"], req.tolerance_px)
                       for e in fixtures_io.list_fixtures()]
    except fixtures_io.FixtureNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown fixture: {exc}") from exc
    n_ok = sum(1 for r in reports if r["category_match"])
    log.info("validate done %d/%d categories matched", n_ok, len(reports))
    return {
        "request_id": rid,
        "versions": _versions(),
        "tolerance_px": req.tolerance_px,
        "n_fixtures": len(reports),
        "n_category_match": n_ok,
        "reports": reports,
    }
