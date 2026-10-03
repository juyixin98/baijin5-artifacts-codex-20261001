"""FastAPI validation interface.

Endpoints
---------
GET  /health              liveness
GET  /v1/version          dependency/version snapshot
POST /v1/seam             one minimum-energy seam (original coordinates)
POST /v1/carve            N seams removed sequentially, original-coordinate
                          paths + per-seam energies + final image
POST /v1/jobs             chunked background carve job
GET  /v1/jobs/{job_id}    job status / result / failure category

Every response uses the envelope defined in ``app.contracts``.  Domain
errors map to their category with HTTP 422 (404 for unknown jobs);
unexpected exceptions map to 500/INTERNAL and are logged with traceback.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.carving import Carver
from app.config import Settings, load_settings
from app.contracts import (
    CarveRequest,
    JobRequest,
    SeamRequest,
    decode_image,
    decode_mask,
    encode_image,
    resolve_options,
    seam_record_to_dict,
    validate_num_seams,
)
from app.errors import SeamCarveError
from app.jobs import JobManager
from app.kernel import find_seam
from app.logging_setup import configure_logging, get_logger, log_event, log_run_started
from app.versions import version_snapshot


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or load_settings()).validate()
    logger = configure_logging(settings.log_level)
    app = FastAPI(title="seamcarve-service", version=version_snapshot()["service"])
    app.state.settings = settings
    app.state.jobs = JobManager(settings, logger)

    @app.exception_handler(SeamCarveError)
    async def domain_error_handler(request: Request, exc: SeamCarveError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        log_event(
            logger,
            logging.WARNING,
            "request_rejected",
            f"{exc.category}: {exc.message}",
            run_id=run_id,
            category=exc.category,
            error=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "ok": False,
                "error": {**exc.to_dict(), "run_id": run_id},
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        logger.exception(
            "unhandled error",
            extra={"event": "request_crashed", "fields": {"run_id": run_id}},
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "category": "INTERNAL",
                    "message": str(exc),
                    "details": {},
                    "run_id": run_id,
                },
            },
        )

    @app.middleware("http")
    async def assign_run_id(request: Request, call_next):
        request.state.run_id = uuid.uuid4().hex[:16]
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict:
        return _ok({"status": "up"})

    @app.get("/v1/version")
    async def version() -> dict:
        return _ok({"versions": version_snapshot()})

    def _prepare(payload, request: Request):
        image, digest = decode_image(payload.image_b64)
        mask = decode_mask(payload, image.shape[:2])
        mode, disp = resolve_options(payload, settings)
        request_settings = Settings(
            energy_mode=mode,
            max_displacement=disp,
            job_chunk_size=settings.job_chunk_size,
            log_level=settings.log_level,
            min_remaining_width=settings.min_remaining_width,
        ).validate()
        log_run_started(
            logger,
            run_id=request.state.run_id,
            input_sha256=digest,
            image_shape=list(image.shape),
            energy_mode=mode,
            max_displacement=disp,
            protected_pixels=int(mask.sum()),
        )
        return image, mask, digest, request_settings

    @app.post("/v1/seam")
    async def seam(payload: SeamRequest, request: Request) -> dict:
        image, mask, digest, req_settings = _prepare(payload, request)
        result = find_seam(image, mask, req_settings)
        log_event(
            logger,
            logging.INFO,
            "seam_selected",
            f"seam energy={result.energy:.6f} start_col={result.start_col}",
            run_id=request.state.run_id,
            input_sha256=digest,
            energy=result.energy,
            energy_mode=result.mode,
            max_displacement=result.max_displacement,
            start_col=result.start_col,
            final_row_ties=result.final_row_ties,
        )
        return _ok(
            {
                "run_id": request.state.run_id,
                "input_sha256": digest,
                "path_original_cols": list(result.path),
                "energy": result.energy,
                "mode": result.mode,
                "max_displacement": result.max_displacement,
                "final_row_ties": result.final_row_ties,
                "versions": version_snapshot(),
            }
        )

    @app.post("/v1/carve")
    async def carve(payload: CarveRequest, request: Request) -> dict:
        image, mask, digest, req_settings = _prepare(payload, request)
        validate_num_seams(
            payload.num_seams, image.shape[1], req_settings.min_remaining_width
        )
        carver = Carver(
            image,
            mask,
            req_settings,
            run_id=request.state.run_id,
            input_sha256=digest,
            logger=logger,
        )
        result = carver.remove_many(payload.num_seams)
        log_event(
            logger,
            logging.INFO,
            "carve_completed",
            f"removed {result.removed} seams",
            run_id=request.state.run_id,
            input_sha256=digest,
            removed=result.removed,
            final_width=int(result.final_image.shape[1]),
        )
        return _ok(
            {
                "run_id": request.state.run_id,
                "input_sha256": digest,
                "original_shape": list(result.original_shape),
                "seams": [seam_record_to_dict(r) for r in result.seams],
                "final_image_b64": encode_image(result.final_image),
                "versions": version_snapshot(),
            }
        )

    @app.post("/v1/jobs", status_code=202)
    async def submit_job(payload: JobRequest, request: Request) -> dict:
        image, mask, digest, req_settings = _prepare(payload, request)
        validate_num_seams(
            payload.num_seams, image.shape[1], req_settings.min_remaining_width
        )
        state = app.state.jobs.submit(
            image=image,
            mask=mask,
            num_seams=payload.num_seams,
            run_id=request.state.run_id,
            input_sha256=digest,
            chunk_size=payload.chunk_size,
            on_seam=seam_record_to_dict,
        )
        return _ok({"job_id": state.job_id, "run_id": state.run_id, "status": state.status})

    @app.get("/v1/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        return _ok(app.state.jobs.get(job_id).to_dict())

    return app


app = create_app()
