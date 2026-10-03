"""FastAPI validation interface.

Endpoints:
    GET  /v1/health             - liveness + engine version
    GET  /v1/profiles           - list registry profile names
    POST /v1/profiles/validate  - validate one profile, return redacted facts
    POST /v1/convert            - convert an image between two ICC profiles

Every response carries a request id and a diagnostics trail explaining
why the request was accepted, rejected, or left undetermined.
"""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import ImageCms

from ..config import Settings, get_settings
from ..contract.enums import ColorSpace
from ..errors import ConversionError, ContractViolationError, ErrorCategory
from ..jobs.chunked import convert_chunked
from ..kernel.engine import ConversionResult, convert_document
from ..profiles.registry import ProfileRegistry
from ..profiles.validate import validate_profile
from .diagnostics import Diagnostics
from .imaging import decode_image, encode_image
from .schemas import ConvertRequest, ProfileRef, ValidateProfileRequest

_HTTP_STATUS = {
    ErrorCategory.ENGINE_FAILURE: 500,
}


def _status_for(exc: ConversionError) -> int:
    return _HTTP_STATUS.get(exc.category, 422)


def _error_body(status: str, exc: ConversionError, request_id: str, diag: Diagnostics) -> dict:
    return {
        "status": status,
        "category": exc.category.value,
        "message": exc.message,
        "request_id": request_id,
        "diagnostics": diag.as_list(),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    registry = ProfileRegistry(settings.profile_dir)
    app = FastAPI(title="iccconv", version="0.1.0")

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ConversionError)
    async def conversion_error_handler(request: Request, exc: ConversionError):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        diag = Diagnostics(request_id)
        diag.record("request", "rejected", exc.message, category=exc.category.value)
        status = "failed" if exc.category is ErrorCategory.ENGINE_FAILURE else "rejected"
        return JSONResponse(
            status_code=_status_for(exc),
            content=_error_body(status, exc, request_id, diag),
        )

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok", "engine": f"littleCMS {ImageCms.versions()[1]}"}

    @app.get("/v1/profiles")
    def list_profiles() -> dict:
        return {"profiles": registry.names()}

    @app.post("/v1/profiles/validate")
    def validate_profile_endpoint(body: ValidateProfileRequest, request: Request) -> dict:
        request_id = request.state.request_id
        diag = Diagnostics(request_id)
        try:
            if body.name is not None:
                data = registry.load_bytes(body.name)
                source = f"registry:{body.name}"
            else:
                assert body.icc_b64 is not None
                try:
                    data = base64.b64decode(body.icc_b64, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ContractViolationError(
                        "icc_b64 is not valid base64", detail={"error": str(exc)[:120]}
                    ) from exc
                source = "inline"
            info = validate_profile(data, source=source)
        except ConversionError as exc:
            diag.record("profile", "rejected", exc.message, category=exc.category.value)
            return JSONResponse(
                status_code=_status_for(exc),
                content=_error_body("rejected", exc, request_id, diag),
            )
        diag.record("profile", "accepted", "profile validated", **info.redacted())
        return {
            "status": "accepted",
            "request_id": request_id,
            "profile": info.redacted() | {"sha256": info.sha256},
            "diagnostics": diag.as_list(),
        }

    def _resolve_profile(ref: ProfileRef, role: str):
        """Return (bytes, ProfileInfo) or None for 'embedded' (source only)."""
        if ref.embedded:
            return None
        if ref.name is not None:
            return registry.load(ref.name)
        data = ref.decode_inline()
        return data, validate_profile(data, source="inline")

    @app.post("/v1/convert")
    def convert(body: ConvertRequest, request: Request):
        request_id = request.state.request_id
        diag = Diagnostics(request_id)
        try:
            try:
                raw = base64.b64decode(body.image_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ContractViolationError(
                    "image_b64 is not valid base64", detail={"error": str(exc)[:120]}
                ) from exc
            doc = decode_image(raw, body.alpha_mode_in)
            if doc.height * doc.width > settings.max_image_pixels:
                raise ContractViolationError(
                    "image exceeds the pixel limit",
                    detail={
                        "pixels": doc.height * doc.width,
                        "max_image_pixels": settings.max_image_pixels,
                    },
                )
            diag.record(
                "decode", "accepted", "image decoded",
                width=doc.width, height=doc.height,
                color_space=doc.color_space.value, alpha_mode=doc.alpha_mode.value,
                has_embedded_profile=doc.embedded_profile is not None,
            )
            source = _resolve_profile(body.source, "source")
            target = _resolve_profile(body.target, "target")
            assert target is not None

            tile_results: list[ConversionResult] = []

            def convert_fn(sub_doc):
                result = convert_document(
                    sub_doc,
                    source=source,
                    target=target,
                    intent=body.intent,
                    black_point_compensation=body.black_point_compensation,
                    alpha_mode_out=body.alpha_mode_out,
                    check_gamut=body.check_gamut,
                    gamut_tolerance=settings.gamut_tolerance,
                    diagnostics=diag,
                )
                tile_results.append(result)
                return result

            job = None
            if body.tile_size is not None:
                out_doc, job = convert_chunked(
                    doc, tile_size=body.tile_size, convert_fn=convert_fn
                )
            else:
                out_doc = convert_fn(doc).document

            first = tile_results[0]
            gamut = None
            if body.check_gamut:
                flagged = sum(r.gamut.flagged_pixels for r in tile_results if r.gamut)
                total = sum(r.gamut.total_pixels for r in tile_results if r.gamut)
                gamut = {
                    "method": "roundtrip-heuristic",
                    "certainty": "heuristic",
                    "tolerance": settings.gamut_tolerance,
                    "flagged_pixels": flagged,
                    "total_pixels": total,
                }

            out_format = body.output_format or (
                "tiff" if out_doc.color_space is ColorSpace.CMYK else "png"
            )
            encoded = encode_image(out_doc, out_format)
            diag.record(
                "encode", "accepted", "output encoded",
                output_format=out_format, output_bytes=len(encoded),
            )
        except ConversionError as exc:
            diag.record("request", "rejected", exc.message, category=exc.category.value)
            status = "failed" if exc.category is ErrorCategory.ENGINE_FAILURE else "rejected"
            return JSONResponse(
                status_code=_status_for(exc),
                content=_error_body(status, exc, request_id, diag),
            )

        return {
            "status": "accepted",
            "request_id": request_id,
            "image_b64": base64.b64encode(encoded).decode("ascii"),
            "output_format": out_format,
            "metadata": {
                "rendering_intent": first.metadata.rendering_intent,
                "black_point_compensation": first.metadata.black_point_compensation,
                "source_profile": first.metadata.source_profile,
                "target_profile": first.metadata.target_profile,
                "lossless": first.metadata.lossless,
                "gamut_note": first.metadata.gamut_note,
            },
            "gamut": gamut,
            "job": None
            if job is None
            else {
                "job_id": job.job_id,
                "status": job.status,
                "tile_size": job.tile_size,
                "tiles": [t.__dict__ for t in job.tiles],
                "result_sha256": job.result_sha256,
            },
            "diagnostics": diag.as_list(),
        }

    return app
