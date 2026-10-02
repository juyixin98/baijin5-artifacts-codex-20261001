"""FastAPI 验证接口。

端点：
- POST /images                      按合成规格构建并发布金字塔
- GET  /images                      列出已发布图像
- GET  /images/{image_id}           图像级元数据（含各层尺寸）
- GET  /images/{image_id}/region    区域查询（format=json|npy|png）
- GET  /health

错误契约：所有领域错误以统一 JSON 返回
{"error": {"category", "message", "run_id", "detail"}}，
category ∈ {input_error, state_conflict, resource_exhausted, compute_failure}。
"""
from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .builder import build_pyramid
from .config import Settings
from .contracts import GeneratorSpec, ImageMeta, RegionRequest
from .errors import (
    InputError,
    LevelNotFoundError,
    PyramidError,
    ResourceExhaustedError,
)
from .kernel import max_levels_for
from .logging_utils import configure_logger, log_event, new_run_id
from .tilestore import TileStore


class CreateImageRequest(BaseModel):
    generator: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    levels: int | None = Field(default=None, ge=1)
    tile_size: int | None = Field(default=None, ge=1)
    params: dict[str, Any] = Field(default_factory=dict)


def _error_payload(exc: PyramidError, run_id: str) -> dict:
    return {
        "error": {
            "category": exc.category.value,
            "message": str(exc),
            "run_id": run_id,
            "detail": exc.detail,
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = configure_logger(settings.log_file)
    store = TileStore(settings.data_root)
    app = FastAPI(title="local-pyramid-service")
    app.state.settings = settings
    app.state.store = store

    @app.exception_handler(PyramidError)
    async def pyramid_error_handler(request: Request, exc: PyramidError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None) or new_run_id()
        log_event(
            logger,
            "request_rejected",
            run_id=run_id,
            category=exc.category.value,
            http_status=exc.http_status,
            reason=str(exc),
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status, content=_error_payload(exc, run_id)
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None) or new_run_id()
        logger.exception(
            "unhandled error",
            extra={"event": "unhandled_error", "fields": {"run_id": run_id}},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "category": "compute_failure",
                    "message": f"internal error: {type(exc).__name__}",
                    "run_id": run_id,
                    "detail": {},
                }
            },
        )

    @app.middleware("http")
    async def run_id_middleware(request: Request, call_next):
        request.state.run_id = new_run_id()
        response = await call_next(request)
        response.headers["X-Run-Id"] = request.state.run_id
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/images", status_code=201)
    def create_image(body: CreateImageRequest, request: Request) -> dict:
        run_id = request.state.run_id
        spec = GeneratorSpec(
            kind=body.generator,
            width=body.width,
            height=body.height,
            params=body.params,
        )
        spec.validate(max_image_pixels=settings.max_image_pixels)
        n_levels = body.levels or min(
            max_levels_for(spec.height, spec.width), settings.max_levels
        )
        if n_levels > settings.max_levels:
            raise ResourceExhaustedError(
                f"levels={n_levels} exceeds max_levels={settings.max_levels}"
            )
        tile_size = body.tile_size or settings.tile_size
        image_id = new_run_id().replace("run-", "img-")
        log_event(
            logger,
            "create_image",
            run_id=run_id,
            image_id=image_id,
            generator=spec.kind,
            width=spec.width,
            height=spec.height,
            levels=n_levels,
            tile_size=tile_size,
        )
        meta = build_pyramid(
            store,
            image_id,
            spec,
            n_levels=n_levels,
            tile_size=tile_size,
            logger=logger,
            run_id=run_id,
        )
        return {"run_id": run_id, "image": meta.to_dict()}

    @app.get("/images")
    def list_images() -> dict:
        return {"images": store.list_images()}

    def _load_image_meta(image_id: str) -> ImageMeta:
        return ImageMeta.from_dict(store.read_image_meta(image_id))

    @app.get("/images/{image_id}")
    def get_image(image_id: str) -> dict:
        return _load_image_meta(image_id).to_dict()

    @app.get("/images/{image_id}/levels/{level}/meta")
    def get_level_meta(image_id: str, level: int) -> dict:
        return store.read_level_meta(image_id, level)

    @app.get("/images/{image_id}/region")
    def get_region(
        image_id: str,
        request: Request,
        level: int = Query(ge=0),
        x: int = Query(ge=0),
        y: int = Query(ge=0),
        w: int = Query(ge=1),
        h: int = Query(ge=1),
        format: str = Query(default="json", pattern="^(json|npy|png)$"),
    ) -> Response:
        run_id = request.state.run_id
        meta = _load_image_meta(image_id)
        lm = meta.level_meta(level)
        if lm is None:
            raise LevelNotFoundError(
                f"level {level} not built for image {image_id!r}; "
                f"available: {[m.level for m in meta.levels]}"
            )
        region = RegionRequest(level=level, x=x, y=y, width=w, height=h)
        region.validate_against(lm)
        if w * h > settings.max_region_pixels:
            raise ResourceExhaustedError(
                f"region {w}x{h} exceeds max_region_pixels={settings.max_region_pixels}"
            )
        if format == "json" and w * h > settings.max_json_pixels:
            raise ResourceExhaustedError(
                f"region {w}x{h} exceeds max_json_pixels={settings.max_json_pixels}; "
                "use format=npy"
            )
        arr = store.read_region(image_id, level, x, y, w, h)
        log_event(
            logger,
            "region_served",
            run_id=run_id,
            image_id=image_id,
            level=level,
            x=x,
            y=y,
            w=w,
            h=h,
            format=format,
        )
        if format == "npy":
            buf = io.BytesIO()
            np.save(buf, arr, allow_pickle=False)
            return Response(content=buf.getvalue(), media_type="application/octet-stream")
        if format == "png":
            from PIL import Image

            clipped = np.clip(arr, 0.0, 255.0).astype(np.uint8)
            buf = io.BytesIO()
            Image.fromarray(clipped, mode="L").save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        return JSONResponse(
            content={
                "run_id": run_id,
                "image_id": image_id,
                "level": level,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "dtype": "float32",
                "data": arr.tolist(),
            }
        )

    return app


app = create_app()
