"""FastAPI validation interface.

Endpoints
---------
GET  /v1/health     -- liveness
GET  /v1/version    -- component versions
POST /v1/seam/find  -- find one minimal seam (original coordinates + energy)
POST /v1/carve      -- remove N seams as a chunked job

Error contract: failures are NEVER returned as success. Expected failures
carry a stable ``error.category`` (see errors.ErrorCategory); unexpected
exceptions become 500/INTERNAL_ERROR with the run_id for log correlation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import VALID_ENERGY_MODES, SeamConfig
from .contracts import image_sha256, validate_pixels, validate_protect_mask
from .errors import ErrorCategory, NoLegalSeamError, SeamCarveError
from .jobs import CarveJob
from .kernel import find_seam
from .runlog import RunLogger, component_versions, new_run_id

app = FastAPI(title="seamcarve", version=__version__)


class FindSeamRequest(BaseModel):
    pixels: list = Field(..., description="HxW or HxWx3 nested lists, ints in [0,255]")
    protect_mask: Optional[list] = Field(None, description="HxW nested lists of 0/1")
    energy_mode: Literal["gradient", "forward"] = "gradient"


class CarveRequest(FindSeamRequest):
    n_seams: int = Field(..., ge=1)
    chunk_size: Optional[int] = Field(None, ge=1)


def _error_body(category: ErrorCategory, detail: str, run_id: str) -> dict:
    return {"error": {"category": category.value, "detail": detail, "run_id": run_id}}


@app.exception_handler(SeamCarveError)
async def seam_carve_error_handler(request: Request, exc: SeamCarveError) -> JSONResponse:
    run_id = getattr(request.state, "run_id", None) or new_run_id()
    status = 409 if isinstance(exc, NoLegalSeamError) else 422
    return JSONResponse(status_code=status, content=_error_body(exc.category, exc.detail, run_id))


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    run_id = new_run_id()
    return JSONResponse(
        status_code=422,
        content=_error_body(ErrorCategory.REQUEST_VALIDATION, str(exc.errors()), run_id),
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    run_id = getattr(request.state, "run_id", None) or new_run_id()
    return JSONResponse(
        status_code=500,
        content=_error_body(ErrorCategory.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}", run_id),
    )


def _config_from(req: FindSeamRequest) -> SeamConfig:
    base = SeamConfig.from_env()
    values = {"energy_mode": req.energy_mode}
    if isinstance(req, CarveRequest) and req.chunk_size is not None:
        values["chunk_size"] = req.chunk_size
    return SeamConfig.from_dict({**base.__dict__, **values})


def _make_run_logger(config: SeamConfig, run_id: str) -> RunLogger:
    log_path = Path(config.log_dir) / f"api-{run_id}.log"
    return RunLogger(run_id=run_id, log_path=log_path)


@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/version")
def version() -> dict:
    return {"versions": component_versions()}


@app.post("/v1/seam/find")
def seam_find(req: FindSeamRequest, request: Request) -> dict:
    run_id = new_run_id()
    request.state.run_id = run_id
    config = _config_from(req)
    log = _make_run_logger(config, run_id)
    image = validate_pixels(req.pixels)
    mask = validate_protect_mask(req.protect_mask, image.shape[:2])
    log.emit(
        "request",
        step="validate",
        endpoint="seam/find",
        image_shape=list(image.shape),
        input_sha256=image_sha256(image),
        energy_mode=config.energy_mode,
        versions=component_versions(),
    )
    result = find_seam(image, mask, config.energy_mode, run_logger=log)
    points = [[row, col] for row, col in enumerate(result.columns)]
    log.emit("response", step="done", endpoint="seam/find", energy=result.energy)
    return {
        "run_id": run_id,
        "input_sha256": image_sha256(image),
        "versions": component_versions(),
        "energy_mode": result.mode,
        "seam": {"points": points, "columns": list(result.columns), "energy": result.energy},
    }


@app.post("/v1/carve")
def carve(req: CarveRequest, request: Request) -> dict:
    run_id = new_run_id()
    request.state.run_id = run_id
    config = _config_from(req)
    image = validate_pixels(req.pixels)
    mask = validate_protect_mask(req.protect_mask, image.shape[:2])
    log_path = Path(config.log_dir) / f"api-{run_id}.log"
    job = CarveJob(image, mask, req.n_seams, config, job_id=run_id, log_path=log_path)
    report = job.run()
    return {
        "run_id": run_id,
        "input_sha256": image_sha256(image),
        "versions": component_versions(),
        "energy_mode": report.energy_mode,
        "original_shape": list(report.original_shape),
        "final_width": report.final_width,
        "seams": [
            {
                "points": [list(p) for p in seam.points],
                "energy": seam.energy,
                "width_after": seam.width_after,
            }
            for seam in report.seams
        ],
        "chunks": list(report.chunks),
    }
