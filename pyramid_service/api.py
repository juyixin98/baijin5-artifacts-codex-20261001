"""HTTP API: build jobs, level metadata, region reads, integrity validation.

Error contract: every :class:`PyramidError` is returned as
``{"error": {"category", "message", "detail"}}`` with the category-specific
HTTP status (400 input, 404 not-found, 409 conflict, 507 resource, 500
compute), so clients can distinguish failure classes without parsing text.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from .builder import BuildSpec, SourceSpec, build_pyramid
from .config import Settings
from .contracts import RegionSpec
from .errors import ErrorCategory, InputValidationError, PyramidError
from .kernel import KERNEL_NAMES
from .observability import log_event, new_run_id
from .patterns import PATTERN_NAMES
from .region import read_region
from .store import TileStore


class SourceModel(BaseModel):
    kind: str = Field(description="'synthetic' or 'npy'")
    pattern: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    channels: int = 1
    seed: Optional[int] = 0
    path: Optional[str] = None


class BuildRequest(BaseModel):
    pyramid_id: Optional[str] = None
    source: SourceModel
    tile_size: int = 128
    levels: int = 0  # 0 -> auto, down to 1x1
    kernel: str = "area"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # Note: the store directory is created lazily by the store on first
    # write, so importing this module has no filesystem side effects.
    store = TileStore(settings.store_dir)
    app = FastAPI(title="pyramid-service", version="1.0.0")

    @app.exception_handler(PyramidError)
    async def pyramid_error_handler(_: Request, exc: PyramidError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Framework-level request errors also use the service envelope.
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "category": ErrorCategory.INPUT.value,
                    "message": "request validation failed",
                    "detail": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Nothing escapes without the declared envelope.
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "category": ErrorCategory.COMPUTE.value,
                    "message": f"unexpected {type(exc).__name__}: {exc}",
                    "detail": None,
                }
            },
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/pyramids", status_code=201)
    def build(req: BuildRequest) -> dict:
        src = req.source
        if src.kind == "synthetic" and src.pattern not in PATTERN_NAMES:
            raise InputValidationError(
                f"unknown pattern {src.pattern!r}; expected one of {PATTERN_NAMES}"
            )
        if req.kernel not in KERNEL_NAMES:
            raise InputValidationError(
                f"unknown kernel {req.kernel!r}; expected one of {KERNEL_NAMES}"
            )
        spec = BuildSpec(
            pyramid_id=req.pyramid_id or f"pyr-{new_run_id()}",
            source=SourceSpec(
                kind=src.kind,
                pattern=src.pattern,
                width=src.width,
                height=src.height,
                channels=src.channels,
                seed=src.seed,
                path=src.path,
            ),
            tile_size=req.tile_size,
            levels=req.levels,
            kernel=req.kernel,
        )
        report = build_pyramid(store, spec, settings)
        return {
            "pyramid_id": report.pyramid_id,
            "run_id": report.run_id,
            "levels": [
                {
                    "level": m.level,
                    "width": m.width,
                    "height": m.height,
                    "tiles": len(m.tiles),
                }
                for m in report.levels
            ],
        }

    @app.get("/pyramids/{pyramid_id}/levels")
    def levels(pyramid_id: str) -> dict:
        metas = [store.load_manifest(pyramid_id, lv) for lv in store.list_levels(pyramid_id)]
        return {
            "pyramid_id": pyramid_id,
            "levels": [
                {
                    "level": m.level,
                    "width": m.width,
                    "height": m.height,
                    "channels": m.channels,
                    "tile_size": m.tile_size,
                    "tiles_x": m.tiles_x,
                    "tiles_y": m.tiles_y,
                    "kernel": m.kernel,
                    "run_id": m.run_id,
                }
                for m in metas
            ],
        }

    @app.get("/pyramids/{pyramid_id}/region")
    def region(
        pyramid_id: str,
        # Bounds are validated in read_region so every input error surfaces
        # through the service's own error taxonomy (400 / input_error).
        level: int = Query(),
        x: int = Query(),
        y: int = Query(),
        w: int = Query(),
        h: int = Query(),
        format: str = "npy",
    ) -> Response:
        run_id = new_run_id()
        if format not in ("npy", "png"):
            raise InputValidationError(
                f"unknown format {format!r}; expected 'npy' or 'png'"
            )
        spec = RegionSpec(level=level, x=x, y=y, w=w, h=h)
        log_event(
            logging.INFO, "region_request", run_id,
            pyramid_id=pyramid_id, level=level, x=x, y=y, w=w, h=h,
            reason="serving region read",
        )
        arr = read_region(store, pyramid_id, spec,
                          max_pixels=settings.max_region_pixels, run_id=run_id)
        if format == "npy":
            buf = io.BytesIO()
            np.save(buf, arr)
            return Response(
                content=buf.getvalue(),
                media_type="application/octet-stream",
                headers={"X-Region-Shape": f"{arr.shape[0]},{arr.shape[1]},{arr.shape[2]}"},
            )
        if format == "png":
            u8 = np.clip(np.rint(arr * 255.0), 0, 255).astype(np.uint8)
            img = Image.fromarray(u8[..., 0] if arr.shape[2] == 1 else u8)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        raise InputValidationError(  # unreachable: format pre-validated
            f"unknown format {format!r}; expected 'npy' or 'png'"
        )

    @app.get("/pyramids/{pyramid_id}/validate")
    def validate(pyramid_id: str, level: Optional[int] = None) -> dict:
        """Re-verify tile integrity (sha256 + shape + dtype) against manifests."""
        levels = [level] if level is not None else store.list_levels(pyramid_id)
        reports = [store.verify_level(pyramid_id, lv) for lv in levels]
        return {
            "pyramid_id": pyramid_id,
            "ok": all(r["ok"] for r in reports),
            "levels": reports,
        }

    return app


app = create_app()
