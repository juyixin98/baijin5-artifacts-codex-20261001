"""FastAPI validation/execution interface.

Endpoints
---------
GET  /health        liveness probe.
GET  /v1/version    component versions (also embedded in EDT responses).
POST /v1/edt        exact Euclidean distance transform of a binary raster.

Every response carries a ``request_id`` (client-supplied or generated)
that also appears in the server logs, so any result or failure can be
traced end to end. Failures use a stable envelope::

    {"error": {"category": "...", "message": "...", "request_id": "..."}}
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .contracts import EdtRequest, encode_distances, encode_labels, grid_to_mask
from .errors import EdtError, ErrorCategory, PayloadTooLargeError
from .logging_setup import get_logger, request_id_var
from .service import compute_edt

logger = get_logger()
settings = get_settings()

app = FastAPI(title="Exact EDT Service", version=__version__)


def _error_response(
    status_code: int, category: str, message: str, request_id: str
) -> JSONResponse:
    logger.warning(
        "request failed: category=%s status=%s message=%s", category, status_code, message
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "category": category,
                "message": message,
                "request_id": request_id,
            }
        },
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    try:
        logger.info("%s %s", request.method, request.url.path)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info("completed status=%s", response.status_code)
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(EdtError)
async def edt_error_handler(request: Request, exc: EdtError) -> JSONResponse:
    return _error_response(
        exc.status_code, exc.category, exc.message, request_id_var.get()
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Schema-level failure (wrong types, missing fields): map the first
    # offending field to the closest stable category.
    first = exc.errors()[0] if exc.errors() else {}
    loc = first.get("loc", ())
    field = str(loc[-1]) if loc else ""
    category = {
        "grid": ErrorCategory.INVALID_GRID,
        "spacing": ErrorCategory.INVALID_SPACING,
        "tile_size": ErrorCategory.INVALID_OPTION,
    }.get(field, ErrorCategory.INVALID_GRID)
    return _error_response(
        422, category, f"request body failed schema validation: {first.get('msg', exc)}",
        request_id_var.get(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return _error_response(
        500, ErrorCategory.INTERNAL_ERROR, f"unexpected error: {type(exc).__name__}",
        request_id_var.get(),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/v1/version")
def version() -> dict:
    from .service import _versions

    return _versions()


@app.post("/v1/edt")
def edt_endpoint(body: EdtRequest) -> dict:
    if body.request_id:
        request_id_var.set(body.request_id)

    mask = grid_to_mask(body.grid)
    n_pixels = mask.size
    if n_pixels > settings.max_grid_pixels:
        raise PayloadTooLargeError(
            f"grid has {n_pixels} pixels, limit is {settings.max_grid_pixels}"
        )

    result = compute_edt(mask, body.spacing, settings, tile_size=body.tile_size)

    return {
        "request_id": request_id_var.get(),
        "shape": [int(mask.shape[0]), int(mask.shape[1])],
        "spacing": [body.spacing[0], body.spacing[1]],
        "mode": result.mode,
        "elapsed_ms": round(result.elapsed_ms, 3),
        "versions": result.versions,
        "tiles": [
            {
                "tile": [t.row0, t.col0, t.row1, t.col1],
                "window": list(t.window),
                "upper_bound": t.upper_bound,
            }
            for t in result.tile_reports
        ],
        "semantics": {
            "distance_null_means": "no source pixel in raster (distance +inf)",
            "label_null_means": "no source pixel in raster",
            "label_encoding": "flat index row * width + col of nearest source",
            "tie_break": "nearest source chosen by (distance, column, row)",
        },
        "distances": encode_distances(result.dist),
        "labels": encode_labels(result.labels),
    }
