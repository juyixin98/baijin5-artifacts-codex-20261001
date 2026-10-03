"""FastAPI service layer for tiled convolution / separable filtering.

Synchronous, local-only execution: fixtures are synthetic, storage is the
local workspace, no external accounts. Every expected failure maps to a
categorised error body ``{"error": {category, message, context}}`` with a
non-2xx status; unknown exceptions surface as ``InternalError``/500 and are
never reported as success.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .contract import BoundaryMode, KernelSpec, digest_array
from .errors import InvalidSpecError, JobStateError, NotFoundError, TileConvError
from .fixtures import synthesize
from .job import TiledJob
from .logging_utils import get_run_logger, versions_snapshot
from .storage import ImageStore
from .validation import validate_output

CATEGORY_HTTP = {
    "InvalidSpec": 422,
    "NotFound": 404,
    "DigestMismatch": 409,
    "JobStateError": 409,
    "InterruptInjected": 500,
    "ValidationFailed": 500,
    "InternalError": 500,
}


# ------------------------------------------------------------------ models
class FixtureImageRequest(BaseModel):
    kind: str
    shape: tuple[int, int]
    seed: int = 0


class KernelRequest(BaseModel):
    kind: str = Field(description='"dense" or "separable"')
    weights: Optional[list[list[float]]] = None
    anchor: Optional[tuple[int, int]] = None
    col_weights: Optional[list[float]] = None
    row_weights: Optional[list[float]] = None
    col_anchor: Optional[int] = None
    row_anchor: Optional[int] = None


class JobRequest(BaseModel):
    image_id: str
    kernel_id: str
    tile_shape: Optional[tuple[int, int]] = None
    boundary: str = "mirror"
    cval: float = 0.0


class RunRequest(BaseModel):
    fail_after: Optional[int] = Field(
        default=None,
        description="test hook: inject a deterministic interrupt after N tiles",
    )


class ValidateRequest(BaseModel):
    atol: Optional[float] = None
    rtol: Optional[float] = None


# ------------------------------------------------------------------ app
def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    store = ImageStore(settings.workspace)
    app = FastAPI(title="tileconv", version=versions_snapshot().get("tileconv", "0.1.0"))

    # ---------------------------------------------------------- error handling
    @app.exception_handler(TileConvError)
    async def tileconv_error_handler(_: Request, exc: TileConvError) -> JSONResponse:
        status = CATEGORY_HTTP.get(exc.category, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"category": "InternalError",
                               "message": f"{type(exc).__name__}: {exc}",
                               "context": {}}},
        )

    # ------------------------------------------------------------- registries
    def kernel_path(kernel_id: str) -> Path:
        return settings.kernels_dir / f"{kernel_id}.json"

    def save_kernel(spec: KernelSpec) -> dict[str, Any]:
        kernel_id = uuid.uuid4().hex[:12]
        record = {"kernel_id": kernel_id, "digest": spec.digest(), "spec": spec.to_dict()}
        kernel_path(kernel_id).write_text(json.dumps(record, indent=1))
        return record

    def load_kernel(kernel_id: str) -> KernelSpec:
        path = kernel_path(kernel_id)
        if not path.exists():
            raise NotFoundError("kernel not found", {"kernel_id": kernel_id})
        return KernelSpec.from_dict(json.loads(path.read_text())["spec"])

    def load_job(job_id: str) -> TiledJob:
        state_path = settings.jobs_dir / job_id / "state.json"
        if not state_path.exists():
            raise NotFoundError("job not found", {"job_id": job_id})
        run_id = json.loads(state_path.read_text())["run_id"]
        return TiledJob(state_path, store, get_run_logger(settings.logs_dir, run_id))

    # -------------------------------------------------------------- endpoints
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/meta/versions")
    def versions() -> dict[str, Any]:
        return {"versions": versions_snapshot()}

    @app.post("/fixtures/images", status_code=201)
    def create_fixture_image(req: FixtureImageRequest) -> dict[str, Any]:
        arr = synthesize(req.kind, req.shape, seed=req.seed)
        spec = store.create_image(arr, meta={"kind": req.kind, "seed": req.seed,
                                             "source": "synthetic-fixture"})
        return {"image_id": spec.image_id, "digest": spec.digest,
                "shape": list(spec.shape), "dtype": spec.dtype}

    @app.get("/images")
    def list_images() -> dict[str, Any]:
        return {"images": store.list_images()}

    @app.post("/kernels", status_code=201)
    def create_kernel(req: KernelRequest) -> dict[str, Any]:
        if req.kind == "dense":
            if req.weights is None:
                raise InvalidSpecError("dense kernel requires 'weights'")
            spec = KernelSpec.dense(req.weights, tuple(req.anchor) if req.anchor else None)
        elif req.kind == "separable":
            if req.col_weights is None or req.row_weights is None:
                raise InvalidSpecError("separable kernel requires 'col_weights' and 'row_weights'")
            spec = KernelSpec.separable(req.col_weights, req.row_weights,
                                        req.col_anchor, req.row_anchor)
        else:
            raise InvalidSpecError(f"unknown kernel kind: {req.kind}")
        record = save_kernel(spec)
        return {"kernel_id": record["kernel_id"], "digest": record["digest"],
                "halo": list(spec.halo()), "anchors": list(spec.anchors_rc())}

    @app.post("/jobs", status_code=201)
    def create_job(req: JobRequest) -> dict[str, Any]:
        image = store.load_spec(req.image_id)
        kernel = load_kernel(req.kernel_id)
        try:
            boundary = BoundaryMode(req.boundary)
        except ValueError:
            raise InvalidSpecError(
                "unknown boundary mode",
                {"boundary": req.boundary,
                 "known": [m.value for m in BoundaryMode]},
            ) from None
        tile_shape = tuple(req.tile_shape) if req.tile_shape else settings.default_tile_shape
        job = TiledJob.create(
            store=store,
            jobs_dir=settings.jobs_dir,
            image=image,
            kernel=kernel,
            boundary=boundary,
            cval=req.cval,
            tile_shape=(int(tile_shape[0]), int(tile_shape[1])),
            logs_dir=settings.logs_dir,
        )
        return {"job_id": job.job_id, "run_id": job.state["run_id"],
                "status": job.status, "progress": job.progress}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = load_job(job_id)
        return {"job_id": job.job_id, "run_id": job.state["run_id"],
                "status": job.status, "progress": job.progress,
                "spec": {k: v for k, v in job.state["spec"].items() if k != "kernel_spec"},
                "versions": job.state["versions"], "error": job.state["error"]}

    @app.post("/jobs/{job_id}/run")
    def run_job(job_id: str, req: RunRequest) -> dict[str, Any]:
        job = load_job(job_id)
        return job.run(fail_after=req.fail_after)

    @app.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str, req: RunRequest) -> dict[str, Any]:
        job = load_job(job_id)
        return job.resume(fail_after=req.fail_after)

    @app.post("/jobs/{job_id}/validate")
    def validate_job(job_id: str, req: ValidateRequest) -> dict[str, Any]:
        job = load_job(job_id)
        if job.status != "completed":
            raise JobStateError("job is not completed; cannot validate",
                                {"job_id": job_id, "status": job.status})
        spec = job.state["spec"]
        kernel = job.kernel_spec()
        image = store.open_array(spec["image_id"])
        output = job.open_output()
        atol = req.atol if req.atol is not None else settings.compare_atol
        rtol = req.rtol if req.rtol is not None else settings.compare_rtol
        report_path = settings.reports_dir / f"validate-{job_id}.json"
        report = validate_output(
            output, image, kernel, BoundaryMode(spec["boundary"]), spec["cval"],
            atol=atol, rtol=rtol,
            context={"job_id": job_id, "run_id": job.state["run_id"],
                     "image_digest": spec["image_digest"],
                     "kernel_digest": spec["kernel_digest"]},
            run_logger=job.run_logger,
            report_path=report_path,
        )
        return report.to_dict()

    @app.get("/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict[str, Any]:
        job = load_job(job_id)
        if job.status != "completed":
            raise JobStateError("job is not completed", {"job_id": job_id,
                                                         "status": job.status})
        import numpy as np

        out = np.lib.format.open_memmap(job.output_path(), mode="r")
        return {"job_id": job_id, "shape": list(out.shape),
                "dtype": str(out.dtype), "output_digest": digest_array(out)}

    return app


app = create_app()
