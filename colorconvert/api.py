"""FastAPI validation/conversion interface.

Thin HTTP layer over :class:`colorconvert.service.ConversionService`.
Image payloads travel as base64-encoded PNG/TIFF; pixel data is never
logged (see ``diagnostics.py``).
"""
from __future__ import annotations

import base64
import binascii
import io
from typing import Literal

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

from .config import Settings
from .contract import ColorMode, ImageData
from .diagnostics import (
    RequestLogger,
    configure_logging,
    fingerprint,
    new_request_id,
)
from .errors import ConversionError, FailureCategory
from .jobs import JobStatus
from .kernel import RenderingIntent
from .profiles import ProfileRegistry
from .service import ConversionRequest, ConversionService

_PIL_TO_MODE = {
    "RGB": (ColorMode.RGB, False),
    "RGBA": (ColorMode.RGB, True),
    "L": (ColorMode.GRAY, False),
    "LA": (ColorMode.GRAY, True),
    "CMYK": (ColorMode.CMYK, False),
}


class ConvertPayload(BaseModel):
    image_b64: str = Field(description="base64-encoded PNG or TIFF")
    source_profile: str = Field(
        description="registry profile id, or 'embedded' to use the image's "
        "own ICC profile (never guessed)"
    )
    target_profile: str = Field(description="registry profile id")
    rendering_intent: str = "relative_colorimetric"
    black_point_compensation: bool = False
    tile_size: int = 512
    alpha_premultiplied: bool = False
    allow_cmyk: bool = False
    output_format: Literal["png", "tiff"] | None = None


def _decode_image(payload: ConvertPayload) -> tuple[ImageData, bytes | None]:
    try:
        raw = base64.b64decode(payload.image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConversionError(
            FailureCategory.IMAGE_DECODE_FAILED,
            f"image_b64 is not valid base64: {exc}",
        ) from exc
    try:
        pil = Image.open(io.BytesIO(raw))
        pil.load()
    except Exception as exc:
        raise ConversionError(
            FailureCategory.IMAGE_DECODE_FAILED,
            f"could not decode image: {exc}",
        ) from exc
    if pil.mode not in _PIL_TO_MODE:
        raise ConversionError(
            FailureCategory.IMAGE_DECODE_FAILED,
            f"unsupported image mode {pil.mode!r}; expected one of "
            + ", ".join(sorted(_PIL_TO_MODE)),
        )
    mode, has_alpha = _PIL_TO_MODE[pil.mode]
    arr = np.asarray(pil, dtype=np.uint8)
    if has_alpha:
        color = arr[..., : mode.channels].copy()
        alpha = arr[..., mode.channels].copy()
    elif mode is ColorMode.GRAY:
        color = arr[:, :, None].copy()
        alpha = None
    else:
        color = arr.copy()
        alpha = None
    embedded = pil.info.get("icc_profile")
    image = ImageData(
        color=color,
        mode=mode,
        alpha=alpha,
        premultiplied=payload.alpha_premultiplied and alpha is not None,
    )
    return image, embedded


def _encode_image(image: ImageData, fmt: str) -> tuple[str, str | None]:
    """Return (image_b64, alpha_b64_or_None)."""
    alpha_b64 = None
    color = image.color
    if image.mode is ColorMode.GRAY:
        pil = Image.fromarray(color[:, :, 0], mode="L")
        if image.alpha is not None and fmt == "png":
            la = np.dstack([color[:, :, 0], image.alpha])
            pil = Image.fromarray(la, mode="LA")
    elif image.mode is ColorMode.RGB:
        if image.alpha is not None and fmt == "png":
            pil = Image.fromarray(
                np.dstack([color, image.alpha]), mode="RGBA"
            )
        else:
            pil = Image.fromarray(color, mode="RGB")
    elif image.mode in (ColorMode.CMYK, ColorMode.LAB):
        pil = Image.fromarray(color, mode=image.mode.value)
        fmt = "tiff"  # PNG cannot carry CMYK/LAB
    else:  # pragma: no cover - defensive
        raise ConversionError(
            FailureCategory.CONTRACT_VIOLATION, f"cannot encode {image.mode}"
        )

    if image.alpha is not None and fmt != "png":
        buf = io.BytesIO()
        Image.fromarray(image.alpha, mode="L").save(buf, format="PNG")
        alpha_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    buf = io.BytesIO()
    pil.save(buf, format="TIFF" if fmt == "tiff" else "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), alpha_b64


def _to_service_request(
    payload: ConvertPayload, settings: Settings
) -> ConversionRequest:
    image, embedded = _decode_image(payload)
    intent = RenderingIntent.parse(payload.rendering_intent)
    return ConversionRequest(
        image=image,
        source_profile=payload.source_profile,
        target_profile=payload.target_profile,
        rendering_intent=intent,
        black_point_compensation=payload.black_point_compensation,
        tile_size=payload.tile_size or settings.default_tile_size,
        allow_cmyk=payload.allow_cmyk,
        embedded_profile=embedded,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = configure_logging(settings.log_level)
    registry = ProfileRegistry.load(settings.registry_path)
    service = ConversionService(registry, settings)

    app = FastAPI(title="colorconvert", version="0.1.0")
    app.state.service = service

    def _log_for(request: Request) -> RequestLogger:
        request_id = request.headers.get("x-request-id") or new_request_id()
        return RequestLogger(logger, request_id)

    def _reject_response(job, log: RequestLogger, status_code: int):
        return JSONResponse(
            status_code=status_code,
            content={"request_id": log.request_id, **job.summary()},
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/v1/profiles")
    def list_profiles():
        return {
            "profiles": [
                {
                    "id": p.profile_id,
                    "color_space": p.color_space,
                    "device_class": p.device_class,
                    "roles": sorted(p.roles),
                    "cmyk_restricted": p.cmyk_restricted,
                    "sha256_12": p.fingerprint,
                    "description": p.description,
                }
                for p in registry
            ]
        }

    @app.post("/v1/validate")
    def validate(payload: ConvertPayload, request: Request):
        log = _log_for(request)
        try:
            svc_req = _to_service_request(payload, settings)
        except ConversionError as exc:
            log.decision("rejected", exc.message, category=exc.category.value)
            return JSONResponse(
                status_code=422,
                content={
                    "request_id": log.request_id,
                    "status": "rejected",
                    "decisions": [
                        {
                            "outcome": "rejected",
                            "reason": exc.message,
                            "category": exc.category.value,
                        }
                    ],
                },
            )
        job = service.validate(svc_req, log)
        code = 200 if job.status is JobStatus.ACCEPTED else 422
        return _reject_response(job, log, code)

    @app.post("/v1/convert")
    def convert(payload: ConvertPayload, request: Request):
        log = _log_for(request)
        try:
            svc_req = _to_service_request(payload, settings)
        except ConversionError as exc:
            log.decision("rejected", exc.message, category=exc.category.value)
            return JSONResponse(
                status_code=422,
                content={
                    "request_id": log.request_id,
                    "status": "rejected",
                    "decisions": [
                        {
                            "outcome": "rejected",
                            "reason": exc.message,
                            "category": exc.category.value,
                        }
                    ],
                },
            )
        log.info(
            "convert request",
            image_fingerprint=fingerprint(payload.image_b64.encode("ascii")),
            image_shape=tuple(svc_req.image.color.shape),
            mode=svc_req.image.mode.name,
            source_profile=payload.source_profile,
            target_profile=payload.target_profile,
        )
        outcome = service.convert(svc_req, log)
        job = outcome.job
        if job.status is not JobStatus.COMPLETED:
            return _reject_response(
                job, log, 422 if job.status is not JobStatus.FAILED else 500
            )
        fmt = payload.output_format or (
            "tiff" if job.result.mode in (ColorMode.CMYK, ColorMode.LAB)
            else "png"
        )
        image_b64, alpha_b64 = _encode_image(job.result, fmt)
        body = {"request_id": log.request_id, **job.summary()}
        body["image_b64"] = image_b64
        body["image_format"] = fmt
        if alpha_b64 is not None:
            body["alpha_b64"] = alpha_b64
        return JSONResponse(status_code=200, content=body)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        log = _log_for(request)
        job = service.jobs.get(job_id)
        if job is None:
            return JSONResponse(
                status_code=404,
                content={
                    "request_id": log.request_id,
                    "error": "job_not_found",
                },
            )
        return {"request_id": log.request_id, **job.summary()}

    return app


app = create_app()
