"""FastAPI surface: /health, /skeletonize, /validate."""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .contracts import ContractError, image_from_png_bytes, validate_binary_image
from .logging_setup import configure_logging, new_request_id, request_id_var
from .service import skeletonize, validate_topology

configure_logging(settings.log_level)
log = logging.getLogger("skeleton.api")

app = FastAPI(title="skeleton-backend", version=__version__)


@app.middleware("http")
async def request_identity(request: Request, call_next):
    rid = request.headers.get("x-request-id") or new_request_id()
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


@app.exception_handler(ContractError)
async def contract_error_handler(request: Request, exc: ContractError):
    log.warning("contract error %s: %s", exc.category, exc.detail)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "category": exc.category,
                "detail": exc.detail,
                "request_id": request_id_var.get(),
            }
        },
    )


class SkeletonizeRequest(BaseModel):
    pixels: list[list[int]] | None = None
    image_png_base64: str | None = None
    mode: str = Field(default="tiled", pattern="^(tiled|full)$")
    tile_size: int | None = Field(default=None, ge=1)


class ValidateRequest(BaseModel):
    original: list[list[int]]
    skeleton: list[list[int]]


def _decode_request_image(body: SkeletonizeRequest) -> np.ndarray:
    if body.pixels is not None:
        return validate_binary_image(np.array(body.pixels))
    if body.image_png_base64 is not None:
        try:
            raw = base64.b64decode(body.image_png_base64, validate=True)
        except binascii.Error as exc:
            raise ContractError("DECODE_FAILED", f"invalid base64: {exc}") from exc
        return image_from_png_bytes(raw)
    raise ContractError("MISSING_IMAGE", "provide either 'pixels' or 'image_png_base64'")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "config": {
            "tile_size": settings.tile_size,
            "max_rounds": settings.max_rounds,
            "tile_workers": settings.tile_workers,
        },
    }


@app.post("/skeletonize")
def post_skeletonize(body: SkeletonizeRequest, request: Request) -> dict[str, Any]:
    img = _decode_request_image(body)
    return skeletonize(
        img,
        mode=body.mode,
        tile_size=body.tile_size,
        request_id=request_id_var.get(),
    )


@app.post("/validate")
def post_validate(body: ValidateRequest, request: Request) -> dict[str, Any]:
    original = validate_binary_image(np.array(body.original))
    skeleton = validate_binary_image(np.array(body.skeleton))
    if original.shape != skeleton.shape:
        raise ContractError(
            "SHAPE_MISMATCH",
            f"original shape {original.shape} != skeleton shape {skeleton.shape}",
        )
    report = validate_topology(original, skeleton)
    report["request_id"] = request_id_var.get()
    return report
