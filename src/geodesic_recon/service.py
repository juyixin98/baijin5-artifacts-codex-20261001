"""FastAPI HTTP interface.

Endpoints:
    GET  /health                 -- liveness + effective configuration
    POST /v1/reconstruct         -- JSON marker/mask -> reconstruction + trace + validation
    POST /v1/validate            -- verify a claimed result against the contract properties
    POST /v1/reconstruct/image   -- multipart PNG marker+mask -> PNG result

Every response carries a request id (``X-Request-ID`` header, echoed if the
client supplies one). Rejections return HTTP 422 with a stable error category.
"""
from __future__ import annotations

import io
from dataclasses import asdict
from typing import Literal

import numpy as np
from fastapi import FastAPI, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from PIL import Image
from pydantic import BaseModel

from .config import Settings, load_settings
from .contracts import validate_pair
from .diagnostics import configure_logging, get_logger, image_stats, new_request_id, set_request_id
from .errors import ContractViolation, FixedPointNotReached
from .kernel import reconstruct
from .validation import check_idempotent, verify_result

logger = get_logger("service")


class ReconstructRequest(BaseModel):
    marker: list[list[float]]
    mask: list[list[float]]
    connectivity: Literal[4, 8] | None = None
    on_violation: Literal["reject", "clip"] | None = None
    algorithm: Literal["sync", "queue", "tiled"] | None = None


class ValidateRequest(BaseModel):
    marker: list[list[float]]
    mask: list[list[float]]
    result: list[list[float]]
    connectivity: Literal[4, 8] | None = None


def _error_response(request_id: str, status: int, category: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"request_id": request_id, "error": {"category": category, "detail": detail}},
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or load_settings()).validate()
    configure_logging()
    app = FastAPI(title="geodesic-recon", version="0.1.0")
    app.state.settings = settings

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        set_request_id(rid)
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(ContractViolation)
    async def contract_violation_handler(request: Request, exc: ContractViolation):
        rid = getattr(request.state, "request_id", "-")
        logger.info("request.rejected category=%s detail=%s", exc.category, exc.detail)
        return _error_response(rid, 422, exc.category, exc.detail)

    @app.exception_handler(FixedPointNotReached)
    async def fixed_point_handler(request: Request, exc: FixedPointNotReached):
        rid = getattr(request.state, "request_id", "-")
        logger.error("request.failed category=NO_CONVERGENCE detail=%s", exc)
        return _error_response(rid, 500, "NO_CONVERGENCE", str(exc))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "config": asdict(settings)}

    def _run_reconstruction(
        marker_in,
        mask_in,
        connectivity: int | None,
        on_violation: str | None,
        algorithm: str | None,
    ) -> dict:
        conn = connectivity or settings.connectivity
        policy = on_violation or settings.on_violation
        algo = algorithm or settings.algorithm

        pair = validate_pair(marker_in, mask_in, on_violation=policy, max_pixels=settings.max_pixels)
        logger.info(
            "request.accepted marker=%s mask=%s clipped=%s algorithm=%s connectivity=%d",
            image_stats(pair.marker), image_stats(pair.mask), pair.clipped, algo, conn,
        )
        result, trace = reconstruct(
            pair.marker,
            pair.mask,
            algorithm=algo,
            connectivity=conn,
            tile_shape=(settings.tile_height, settings.tile_width),
        )
        report = verify_result(pair.marker, pair.mask, result, conn)
        logger.info(
            "request.completed trace=%s validation_ok=%s",
            asdict(trace), report.ok,
        )
        return {
            "clipped": pair.clipped,
            "result": result.tolist(),
            "trace": asdict(trace),
            "validation": report.to_dict(),
        }

    @app.post("/v1/reconstruct")
    def reconstruct_endpoint(req: ReconstructRequest, request: Request) -> dict:
        out = _run_reconstruction(req.marker, req.mask, req.connectivity, req.on_violation, req.algorithm)
        out["request_id"] = getattr(request.state, "request_id", "-")
        return out

    @app.post("/v1/validate")
    def validate_endpoint(req: ValidateRequest, request: Request) -> dict:
        conn = req.connectivity or settings.connectivity
        pair = validate_pair(
            req.marker, req.mask, on_violation="reject", max_pixels=settings.max_pixels
        )
        result = np.asarray(req.result, dtype=np.float64)
        report = verify_result(pair.marker, pair.mask, result, conn)
        idem = check_idempotent(pair.marker, pair.mask, algorithm="queue", connectivity=conn)
        body = report.to_dict()
        body["checks"].append(asdict(idem))
        body["ok"] = body["ok"] and idem.passed
        body["request_id"] = getattr(request.state, "request_id", "-")
        logger.info("validate.completed ok=%s", body["ok"])
        return body

    @app.post("/v1/reconstruct/image")
    async def reconstruct_image(
        marker: UploadFile,
        mask: UploadFile,
        request: Request,
        connectivity: Literal[4, 8] | None = Query(default=None),
        on_violation: Literal["reject", "clip"] | None = Query(default=None),
        algorithm: Literal["sync", "queue", "tiled"] | None = Query(default=None),
    ) -> Response:
        def _decode(upload: UploadFile, data: bytes, name: str) -> np.ndarray:
            try:
                img = Image.open(io.BytesIO(data))
                if img.mode not in ("L", "I", "I;16", "F"):
                    img = img.convert("L")
                return np.asarray(img)
            except Exception as exc:
                raise ContractViolation("MALFORMED_IMAGE", f"{name}: not a decodable image ({exc})") from exc

        marker_arr = _decode(marker, await marker.read(), "marker")
        mask_arr = _decode(mask, await mask.read(), "mask")
        out = _run_reconstruction(marker_arr, mask_arr, connectivity, on_violation, algorithm)

        result = np.asarray(out["result"], dtype=np.float64)
        if result.min() < 0 or result.max() > 65535:
            raise ContractViolation(
                "RESULT_NOT_ENCODABLE", "result values outside [0, 65535] cannot be encoded as 16-bit PNG"
            )
        png = Image.fromarray(np.rint(result).astype(np.uint16), mode="I;16")
        buf = io.BytesIO()
        png.save(buf, format="PNG")
        rid = getattr(request.state, "request_id", "-")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"X-Request-ID": rid, "X-Clipped": str(out["clipped"]).lower()},
        )

    return app


app = create_app()
