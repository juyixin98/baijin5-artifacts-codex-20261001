"""FastAPI verification interface.

Endpoints
---------
``GET  /health``                 - liveness + versions
``POST /v1/edt``                 - compute EDT on an uploaded PNG
``POST /v1/edt/verify``          - same, but also check against the
                                   independent brute-force reference for
                                   small rasters and report discrepancies
                                   as separate failure items

Error semantics
---------------
* 400 INVALID_IMAGE / INVALID_SPACING / INVALID_SHAPE - caller's fault,
  fix the request; the body ``failures`` list carries a location.
* 413 TOO_LARGE - raster exceeds configured limits.
* 500 INTERNAL_ERROR - unexpected server-side failure; the request id is
  echoed so logs can be correlated.

Empty results are *successful* responses: no sources yields finite stats
with ``has_sources=false`` and +inf distances, never an error.
"""
from __future__ import annotations

import logging
import uuid

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import KERNEL_VERSION, __version__
from .contracts import (
    EDTRequest,
    EDTResponse,
    FailureItem,
    HealthResponse,
    StatsModel,
    WarningItem,
)
from .io_image import (
    encode_float32_png_b64,
    encode_int32_png_b64,
)
from .reference import brute_force_edt
from .service import ServiceError, logger, run_edt, stats_from_result

MAX_VERIFY_CELLS = 10_000

app = FastAPI(
    title="Exact Euclidean Distance Transform Service",
    version=__version__,
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="edt-service",
        version=__version__,
        kernel_version=KERNEL_VERSION,
    )


def _grid_float(a: np.ndarray) -> list[list[float]]:
    return [[None if not np.isfinite(v) else float(v) for v in row]
            for row in a.tolist()]


def _build_response(
    req: EDTRequest,
    output,
    include_grids: bool,
    extra_failures: list[FailureItem] | None = None,
    extra_warnings: list[WarningItem] | None = None,
    extra_execution: dict | None = None,
) -> EDTResponse:
    r = output.result
    stats = StatsModel(**stats_from_result(r))
    execution = {
        "path": "tiled" if output.tiling else "direct",
        "elapsed_ms": output.elapsed_ms,
        "steps": output.steps,
        "tiles": len(output.tiling.windows) if output.tiling else 0,
        "tiling_reason": output.tiling.reason if output.tiling else None,
        "manhattan_distance_bound": (
            output.tiling.manhattan_max if output.tiling else None
        ),
    }
    if extra_execution:
        execution.update(extra_execution)

    # +inf is not JSON; represent no-source cells as null.
    grid = _grid_float(r.distances) if include_grids else None
    return EDTResponse(
        request_id=output.request_id,
        kernel_version=KERNEL_VERSION,
        spacing_y=r.spacing_y,
        spacing_x=r.spacing_x,
        stats=stats,
        distance_png_base64=encode_float32_png_b64(r.distances.astype(np.float32)),
        nearest_y_png_base64=encode_int32_png_b64(r.nearest_y.astype(np.int32)),
        nearest_x_png_base64=encode_int32_png_b64(r.nearest_x.astype(np.int32)),
        distance_grid=grid,
        nearest_y_grid=(
            r.nearest_y.tolist() if include_grids else None
        ),
        nearest_x_grid=(
            r.nearest_x.tolist() if include_grids else None
        ),
        execution=execution,
        failures=extra_failures or [],
        warnings=[WarningItem(**w) for w in output.warnings] + (extra_warnings or []),
    )


def _error(status: int, code: str, message: str, request_id: str,
           location: str = "") -> JSONResponse:
    logger.log(
        logging.ERROR if status >= 500 else logging.WARNING,
        f"request_id={request_id} code={code} {message}",
    )
    return JSONResponse(
        status_code=status,
        content={
            "request_id": request_id,
            "kernel_version": KERNEL_VERSION,
            "failures": [
                {"code": code, "message": message, "location": location}
            ],
            "warnings": [],
        },
    )


@app.post("/v1/edt", response_model=EDTResponse)
def compute_edt(req: EDTRequest):
    rid = req.request_id or f"req-{uuid.uuid4().hex[:12]}"
    try:
        output = run_edt(
            req.image_base64, req.spacing_y, req.spacing_x,
            req.source_value, req.force_tiled, req.request_id,
        )
        rid = output.request_id
    except ServiceError as exc:
        status = 413 if exc.code == "TOO_LARGE" else 400
        return _error(status, exc.code, exc.message, rid, exc.location)
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("unexpected failure request_id=%s", rid)
        return _error(500, "INTERNAL_ERROR", str(exc), rid)

    h, w = output.mask.shape
    include_grids = h * w <= MAX_VERIFY_CELLS
    return _build_response(req, output, include_grids)


@app.post("/v1/edt/verify", response_model=EDTResponse)
def verify_edt(req: EDTRequest):
    """Compute AND independently verify small rasters against brute force."""
    rid = req.request_id or f"req-{uuid.uuid4().hex[:12]}"
    try:
        output = run_edt(
            req.image_base64, req.spacing_y, req.spacing_x,
            req.source_value, req.force_tiled, req.request_id,
        )
        rid = output.request_id
    except ServiceError as exc:
        status = 413 if exc.code == "TOO_LARGE" else 400
        return _error(status, exc.code, exc.message, rid, exc.location)
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("unexpected failure request_id=%s", rid)
        return _error(500, "INTERNAL_ERROR", str(exc), rid)

    h, w = output.mask.shape
    failures: list[FailureItem] = []
    warnings: list[WarningItem] = []
    verification: dict = {"performed": False}
    if h * w > MAX_VERIFY_CELLS:
        warnings.append(WarningItem(
            code="VERIFICATION_SKIPPED",
            message=(
                f"raster has {h * w} cells > {MAX_VERIFY_CELLS}; "
                "independent brute-force verification was skipped"
            ),
        ))
    else:
        verification = _run_verification(output, failures)

    return _build_response(
        req, output, include_grids=True,
        extra_failures=failures, extra_warnings=warnings,
        extra_execution={"verification": verification},
    )


def _run_verification(output, failures: list[FailureItem]) -> dict:
    ref = brute_force_edt(
        output.mask, output.result.spacing_y, output.result.spacing_x
    )
    d = output.result.distances
    ny = output.result.nearest_y
    nx = output.result.nearest_x
    h, w = d.shape

    dist_bad: list[dict] = []
    coord_bad: list[dict] = []
    max_rel = 0.0
    checked = 0
    for y in range(h):
        for x in range(w):
            rv = ref.distances[y][x]
            kv = float(d[y, x])
            checked += 1
            if np.isinf(rv):
                if not np.isinf(kv):
                    dist_bad.append({"y": y, "x": x, "expected": None,
                                     "actual": kv})
                continue
            denom = max(rv, 1e-12)
            rel = abs(kv - rv) / denom
            max_rel = max(max_rel, rel)
            if rel > 1e-6:
                dist_bad.append({"y": y, "x": x, "expected": rv,
                                 "actual": kv})
            if (int(ny[y, x]), int(nx[y, x])) != (
                ref.nearest_y[y][x], ref.nearest_x[y][x]
            ):
                coord_bad.append({
                    "y": y, "x": x,
                    "expected": [ref.nearest_y[y][x], ref.nearest_x[y][x]],
                    "actual": [int(ny[y, x]), int(nx[y, x])],
                })

    # Keep payloads bounded: report counts plus the first offending pixels.
    result = {
        "performed": True,
        "method": "independent_brute_force_pixel_loop",
        "pixels_checked": checked,
        "max_relative_distance_error": max_rel,
        "distance_mismatch_count": len(dist_bad),
        "nearest_source_mismatch_count": len(coord_bad),
        "first_distance_mismatches": dist_bad[:5],
        "first_coordinate_mismatches": coord_bad[:5],
    }
    if dist_bad:
        failures.append(FailureItem(
            code="VERIFICATION_DISTANCE_MISMATCH",
            message=(
                f"{len(dist_bad)} pixel(s) disagree with the independent "
                "reference; see execution.verification"
            ),
            location="kernel.distances",
        ))
    if coord_bad:
        failures.append(FailureItem(
            code="VERIFICATION_NEAREST_SOURCE_MISMATCH",
            message=(
                f"{len(coord_bad)} pixel(s) map to a different nearest "
                "source than the independent reference"
            ),
            location="kernel.nearest_source",
        ))
    return result
