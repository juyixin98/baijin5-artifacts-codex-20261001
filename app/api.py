"""FastAPI validation service.

Endpoints
---------
- ``GET  /health``                — liveness + dependency versions
- ``POST /jobs``                  — create a tiled filtering job
- ``GET  /jobs/{job_id}``         — status and progress
- ``POST /jobs/{job_id}/run``     — execute (optional ``max_tiles`` to
                                    simulate an interrupt)
- ``POST /jobs/{job_id}/resume``  — resume an interrupted job (digest-bound)
- ``GET  /jobs/{job_id}/report``  — completion report (peak RSS, digests)
- ``POST /validate``              — run a spec tiled AND via independent
                                    references, return the pixel-exact verdict

Error model: domain failures map to stable categories (``unknown_job`` 404,
``digest_mismatch`` 409, ``invalid_spec`` 422, ``job_state`` 409).  Unexpected
failures surface as 500 with category ``internal`` — never as a fake success.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, get_settings
from .contract import BoundaryMode, ContractError
from .jobs import (
    JobError,
    JobSpec,
    JobStateError,
    TiledJobRunner,
    build_image,
    build_kernel,
    load_runner,
)
from .jobs import _canonical  # canonical encoding shared with job identity
from .kernels import filter_direct, filter_separable_direct
from .logging_utils import versions
from .reference import scipy_reference

_STATUS_BY_CATEGORY = {
    "unknown_job": 404,
    "digest_mismatch": 409,
    "invalid_spec": 422,
    "job_state": 409,
}


class JobRequest(BaseModel):
    image: dict[str, Any]
    kernel: dict[str, Any]
    boundary: str = "mirror"
    cval: float = 0.0
    tile: list[int] = Field(default_factory=lambda: [512, 512])


class RunRequest(BaseModel):
    max_tiles: int | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="tiled-filter-service", version=__version__)
    app.state.settings = settings

    @app.exception_handler(JobError)
    async def job_error_handler(_: Request, exc: JobError) -> JSONResponse:
        status = _STATUS_BY_CATEGORY.get(exc.category, 400)
        return JSONResponse(
            status_code=status,
            content={"status": "error", "category": exc.category, "detail": str(exc)},
        )

    @app.exception_handler(ContractError)
    async def contract_error_handler(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "category": "invalid_spec",
                "detail": str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "category": "internal",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )

    def _runner(body: JobRequest) -> TiledJobRunner:
        spec = JobSpec.from_dict(body.model_dump())
        return TiledJobRunner(spec=spec, settings=settings)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service_version": __version__, "versions": versions()}

    @app.post("/jobs", status_code=201)
    def create_job(body: JobRequest) -> dict[str, Any]:
        manifest = _runner(body).create()
        return {
            "job_id": manifest["job_id"],
            "status": manifest["status"],
            "tiles_total": len(manifest["tiles"]),
            "input_digest": manifest["input_digest"],
            "kernel_digest": manifest["kernel_digest"],
        }

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        runner = load_runner(job_id, settings)
        manifest = runner._load_manifest()
        return runner._status_payload(manifest, "ok")

    @app.post("/jobs/{job_id}/run")
    def run_job(job_id: str, body: RunRequest | None = None) -> dict[str, Any]:
        runner = load_runner(job_id, settings)
        max_tiles = body.max_tiles if body else None
        return runner.execute(max_tiles=max_tiles)

    @app.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str, body: RunRequest | None = None) -> dict[str, Any]:
        runner = load_runner(job_id, settings)
        max_tiles = body.max_tiles if body else None
        return runner.execute(max_tiles=max_tiles)

    @app.get("/jobs/{job_id}/report")
    def job_report(job_id: str) -> dict[str, Any]:
        runner = load_runner(job_id, settings)
        return runner.report()

    @app.post("/validate")
    def validate(body: JobRequest) -> dict[str, Any]:
        """Run tiled and independent references; compare pixel by pixel.

        The verdict carries the actual error metrics and the tolerance used,
        so the judgement basis is visible, not just a boolean.
        """
        spec = JobSpec.from_dict(body.model_dump())
        settings_ws = settings
        # 1. tiled execution in a fresh workspace job
        runner = TiledJobRunner(spec=spec, settings=settings_ws)
        try:
            runner.create()
        except JobStateError:
            pass  # identical spec already materialised: reuse it
        runner.execute()
        tiled = runner.result_array()

        # 2. references (independent of the tiled path)
        image = build_image(spec.image, settings_ws)
        kernel = build_kernel(spec.kernel, settings_ws)
        boundary = BoundaryMode.parse(spec.boundary)
        direct = (
            filter_separable_direct(image.data, kernel, boundary, spec.cval)
            if spec.kernel.get("kind") in ("separable", "separable_gaussian")
            else filter_direct(image.data, kernel, boundary, spec.cval)
        )
        scipy_ref = scipy_reference(image.data, kernel, boundary, spec.cval)

        tol = settings_ws.compare_tolerance
        err_tiled_direct = float(np.max(np.abs(tiled - direct)))
        err_tiled_scipy = float(np.max(np.abs(tiled - scipy_ref)))
        err_direct_scipy = float(np.max(np.abs(direct - scipy_ref)))
        passed = (
            err_tiled_direct <= tol
            and err_tiled_scipy <= tol
            and err_direct_scipy <= tol
        )
        return {
            "status": "pass" if passed else "fail",
            "job_id": runner.compute_job_id(),
            "spec_digest": _canonical(spec.to_dict()),
            "tolerance": tol,
            "errors": {
                "tiled_vs_direct": err_tiled_direct,
                "tiled_vs_scipy": err_tiled_scipy,
                "direct_vs_scipy": err_direct_scipy,
            },
            "basis": (
                "max-abs pixel error over the full image; tiled output must "
                "match both the single-pass implementation and the "
                "scipy.ndimage reference within tolerance"
            ),
        }

    return app


app = create_app()
