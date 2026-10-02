"""HTTP boundary: FastAPI application exposing the reconstruction service.

Endpoints
---------
GET  /health            liveness probe
POST /v1/validate       contract check only, returns a ValidationReport
POST /v1/reconstruct    full reconstruction; JSON envelope by default,
                        raw PNG with ``response_format=png``

Both POST endpoints take multipart/form-data with two PNG file fields,
``marker`` and ``mask``, plus query options.  Every response (success or
failure) carries a Diagnostics payload with a request id; the same id
appears in the server log stream.
"""

from __future__ import annotations

import base64
from typing import Annotated

import numpy as np
from fastapi import Depends, FastAPI, File, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .diagnostics import configure_logging, new_request_id
from .imaging import ImageContractError, decode_png, encode_png
from .schemas import (
    Connectivity,
    Diagnostics,
    Engine,
    ErrorResponse,
    FailureCategory,
    JobStatus,
    ReconstructionResult,
    ValidationReport,
    ViolationPolicy,
)
from .service import ReconstructionRejected, reconstruct, validate_only

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB per file, decoded-size limits apply too


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    app = FastAPI(title="geodesic-reconstruction", version="0.1.0")
    app.state.settings = settings

    def _settings() -> Settings:
        return app.state.settings

    async def _read_upload(upload: UploadFile, label: str) -> bytes:
        payload = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ImageContractError(
                FailureCategory.IMAGE_TOO_LARGE,
                f"{label}: upload exceeds {MAX_UPLOAD_BYTES} bytes",
            )
        return payload

    def _error_response(
        status_code: int, diag: Diagnostics, detail: str
    ) -> JSONResponse:
        body = ErrorResponse(diagnostics=diag, detail=detail)
        return JSONResponse(
            status_code=status_code,
            content=body.model_dump(mode="json"),
            headers={"X-Request-Id": diag.request_id},
        )

    async def _decode_pair(
        marker: UploadFile, mask: UploadFile, request_id: str
    ) -> tuple[np.ndarray, np.ndarray]:
        """Decode both uploads; converts contract errors into HTTP 400."""
        from .diagnostics import log_diagnostics

        decoded: dict[str, np.ndarray] = {}
        for label, upload in (("marker", marker), ("mask", mask)):
            try:
                payload = await _read_upload(upload, label)
                decoded[label] = decode_png(payload, label=label)
            except ImageContractError as exc:
                diag = Diagnostics(
                    request_id=request_id,
                    status=JobStatus.UNDETERMINED,
                    reasons=[exc.message],
                    failure_category=exc.category,
                )
                log_diagnostics(diag)
                raise _HttpError(
                    400, diag, f"cannot decode {label}: {exc.message}"
                ) from exc
        return decoded["marker"], decoded["mask"]

    class _HttpError(Exception):
        def __init__(self, status_code: int, diag: Diagnostics, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.diag = diag
            self.detail = detail

    @app.exception_handler(_HttpError)
    async def _http_error_handler(_request, exc: _HttpError) -> JSONResponse:
        return _error_response(exc.status_code, exc.diag, exc.detail)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/validate", response_model=ValidationReport)
    async def validate(
        marker: Annotated[UploadFile, File()],
        mask: Annotated[UploadFile, File()],
        connectivity: Annotated[Connectivity, Query()] = Connectivity(
            settings.default_connectivity
        ),
        on_violation: Annotated[ViolationPolicy, Query()] = ViolationPolicy(
            settings.default_on_violation
        ),
        cfg: Settings = Depends(_settings),
    ) -> ValidationReport:
        request_id = new_request_id()
        marker_arr, mask_arr = await _decode_pair(marker, mask, request_id)
        diag = validate_only(
            marker_arr,
            mask_arr,
            connectivity=connectivity,
            on_violation=on_violation,
            settings=cfg,
            request_id=request_id,
        )
        stats = lambda a: {  # noqa: E731 - tiny local helper
            "min": int(a.min()),
            "max": int(a.max()),
            "nonzero": int(np.count_nonzero(a)),
        }
        return ValidationReport(
            diagnostics=diag,
            marker_stats=stats(marker_arr),
            mask_stats=stats(mask_arr),
        )

    @app.post("/v1/reconstruct")
    async def reconstruct_endpoint(
        marker: Annotated[UploadFile, File()],
        mask: Annotated[UploadFile, File()],
        engine: Annotated[Engine, Query()] = Engine.QUEUE,
        connectivity: Annotated[Connectivity, Query()] = Connectivity(
            settings.default_connectivity
        ),
        on_violation: Annotated[ViolationPolicy, Query()] = ViolationPolicy(
            settings.default_on_violation
        ),
        response_format: Annotated[str, Query(pattern="^(json|png)$")] = "json",
        cfg: Settings = Depends(_settings),
    ) -> Response:
        request_id = new_request_id()
        marker_arr, mask_arr = await _decode_pair(marker, mask, request_id)
        try:
            result, diag = reconstruct(
                marker_arr,
                mask_arr,
                connectivity=connectivity,
                engine=engine,
                on_violation=on_violation,
                settings=cfg,
                request_id=request_id,
            )
        except ReconstructionRejected as exc:
            return _error_response(422, exc.diagnostics, str(exc))

        png = encode_png(result)
        headers = {"X-Request-Id": diag.request_id}
        if response_format == "png":
            headers["X-Diagnostics"] = diag.model_dump_json()
            return Response(content=png, media_type="image/png", headers=headers)
        envelope = ReconstructionResult(
            diagnostics=diag,
            result_png_b64=base64.b64encode(png).decode("ascii"),
            result_shape=list(result.shape),
            result_dtype=str(result.dtype),
        )
        return JSONResponse(
            content=envelope.model_dump(mode="json"), headers=headers
        )

    return app


app = create_app()
