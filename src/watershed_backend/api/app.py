"""FastAPI validation interface.

Endpoints
---------
GET  /v1/health                 liveness
GET  /v1/version                runtime versions that influence output
POST /v1/jobs                   submit + run a segmentation job (JSON arrays)
POST /v1/segment/image          submit + run with a base64 PNG gradient
GET  /v1/jobs/{job_id}          job status, stages, progress, error
GET  /v1/jobs/{job_id}/result   segmentation result (409 while failed/pending)

Failures are surfaced, never flattened to success: contract violations map
to 422 with a machine-readable ``error.category``; unknown job ids to 404;
results of non-completed jobs to 409.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import Settings, load_settings
from ..errors import ContractViolation, KernelError
from ..imageio import decode_gradient_png
from ..jobs import COMPLETED, JobRunner, JobStore
from ..logging_utils import get_logger
from ..version import runtime_versions
from .schemas import ImageSegmentRequest, SegmentRequest


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logger = get_logger(settings.log_level)
    store = JobStore()
    runner = JobRunner(store, settings, logger)

    app = FastAPI(title="watershed-backend", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner

    @app.exception_handler(ContractViolation)
    async def contract_violation_handler(_: Request, exc: ContractViolation):
        return JSONResponse(status_code=422, content={"error": exc.to_dict()})

    @app.exception_handler(KernelError)
    async def kernel_error_handler(_: Request, exc: KernelError):
        return JSONResponse(status_code=500, content={"error": exc.to_dict()})

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/version")
    def version() -> dict:
        return runtime_versions()

    @app.post("/v1/jobs", status_code=201)
    def submit_job(payload: SegmentRequest) -> dict:
        request = payload.model_dump(exclude_none=True)
        record = runner.run(runner.submit(request).job_id)
        return _job_response(record)

    @app.post("/v1/segment/image", status_code=201)
    def submit_image_job(payload: ImageSegmentRequest) -> dict:
        try:
            gradient = decode_gradient_png(base64.b64decode(payload.gradient_png_b64))
        except (binascii.Error, ValueError, OSError) as exc:
            # OSError covers PIL.UnidentifiedImageError for non-PNG bytes.
            raise ContractViolation(f"invalid gradient PNG: {exc}") from exc
        request = {
            "gradient": gradient.tolist(),
            "seeds": [s.model_dump() for s in payload.seeds],
        }
        if payload.connectivity is not None:
            request["connectivity"] = payload.connectivity
        record = runner.run(runner.submit(request).job_id)
        return _job_response(record)

    @app.get("/v1/jobs/{job_id}")
    def job_status(job_id: str) -> JSONResponse:
        record = store.get(job_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"category": "JOB_NOT_FOUND", "message": job_id}},
            )
        return JSONResponse(content=record.to_dict(include_result=False))

    @app.get("/v1/jobs/{job_id}/result")
    def job_result(job_id: str) -> JSONResponse:
        record = store.get(job_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"category": "JOB_NOT_FOUND", "message": job_id}},
            )
        if record.status != COMPLETED:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "category": "JOB_NOT_COMPLETED",
                        "message": f"job is {record.status}",
                    },
                    "job": record.to_dict(include_result=False),
                },
            )
        return JSONResponse(content=record.to_dict(include_result=True))

    return app


def _job_response(record) -> JSONResponse:
    # A failed job is still a processed request: 422 for contract failures,
    # 500 for kernel-internal failures, 201 only on clean completion.
    if record.status == COMPLETED:
        return JSONResponse(status_code=201, content=record.to_dict())
    status_code = 500
    if record.error and record.error.get("category") not in (None, "INTERNAL", "KERNEL_INVARIANT"):
        status_code = 422
    return JSONResponse(status_code=status_code, content=record.to_dict())


app = create_app()
