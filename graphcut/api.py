"""HTTP interface: FastAPI app exposing the segmentation service.

Endpoints:
    GET  /health                     liveness
    GET  /v1/fixtures                list local sample fixtures
    POST /v1/segment                 synchronous solve, returns labeling +
                                     energy decomposition + cut certificate
    POST /v1/jobs                    submit a chunked background job
    GET  /v1/jobs/{job_id}           poll job state / fetch result
    POST /v1/jobs/{job_id}/cancel    cooperative cancellation
    POST /v1/validate                independent energy evaluation of a
                                     caller-supplied labeling

Every response (success or error) carries a ``run_id`` that also appears in
the structured logs, so any run can be replayed from the log stream.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import AppConfig
from .energy import evaluate_energy
from .errors import GraphCutError, InputValidationError
from .jobs import JobManager, result_to_dict
from .logging_utils import configure_logging, get_logger, log_event
from .models import ImageContract
from .pipeline import run_pipeline
from .specs import build_spec

PNG_SUFFIX = ".png"


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    configure_logging(config.log_level)
    logger = get_logger()
    manager = JobManager(config)

    app = FastAPI(title="graphcut-segmentation", version="1.0.0")
    app.state.config = config
    app.state.jobs = manager

    @app.exception_handler(GraphCutError)
    async def graphcut_error_handler(
        request: Request, exc: GraphCutError
    ) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None) or "unknown"
        log_event(
            logger, logging.WARNING, run_id, "request_rejected",
            category=exc.category.value, code=exc.code, reason=exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"run_id": run_id, "error": exc.to_dict()},
        )

    @app.middleware("http")
    async def assign_run_id(request: Request, call_next):
        request.state.run_id = uuid.uuid4().hex[:12]
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/fixtures")
    def fixtures() -> dict[str, Any]:
        data_dir = config.data_dir
        files = sorted(
            p.name for p in data_dir.iterdir() if p.is_file()
        ) if data_dir.is_dir() else []
        return {"data_dir": str(data_dir), "files": files}

    @app.post("/v1/segment")
    def segment(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        run_id = request.state.run_id
        spec = _spec_from_payload(payload, config)
        result = run_pipeline(spec, config, run_id=run_id, logger=logger)
        return {"run_id": run_id, "result": result_to_dict(result)}

    @app.post("/v1/jobs", status_code=202)
    def submit_job(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        spec = _spec_from_payload(payload, config)
        record = manager.submit(spec)
        log_event(
            logger, logging.INFO, record.job_id, "job_submitted",
            width=spec.width, height=spec.height,
        )
        return record.to_dict()

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return manager.get(job_id).to_dict()

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return manager.cancel(job_id).to_dict()

    @app.post("/v1/validate")
    def validate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        """Independently score a labeling against a spec.

        Never touches the solver: this is the reference path used to
        cross-check results. If ``claimed_energy`` is supplied, the
        independently computed value is compared against it.
        """
        run_id = request.state.run_id
        spec_payload = payload.get("spec")
        if not isinstance(spec_payload, dict):
            raise InputValidationError(
                "bad_request", "'spec' must be an object"
            )
        spec = _spec_from_payload(spec_payload, config)
        labeling = payload.get("labeling")
        if labeling is None:
            raise InputValidationError(
                "bad_request", "'labeling' is required"
            )
        energy = evaluate_energy(spec, _as_labeling(labeling, spec))
        response: dict[str, Any] = {
            "run_id": run_id,
            "energy": {
                "data": energy.data,
                "smoothness": energy.smoothness,
                "total": energy.total,
            },
        }
        claimed = payload.get("claimed_energy")
        if claimed is not None:
            claimed_value = float(claimed)
            response["claimed_energy"] = claimed_value
            response["matches_claim"] = bool(
                abs(claimed_value - energy.total) <= config.flow_tolerance
            )
        log_event(
            logger, logging.INFO, run_id, "validation_done",
            energy=round(energy.total, 6),
        )
        return response

    return app


def _spec_from_payload(payload: dict[str, Any], config: AppConfig):
    if not isinstance(payload, dict):
        raise InputValidationError("bad_request", "request body must be an object")
    payload = dict(payload)
    image_id = payload.pop("image_id", None)
    if image_id is not None:
        payload["_image"] = _load_fixture_image(image_id, config.data_dir)
    return build_spec(payload)


def _load_fixture_image(image_id: str, data_dir: Path) -> ImageContract:
    name = str(image_id)
    if "/" in name or ".." in name or not name.endswith(PNG_SUFFIX):
        raise InputValidationError(
            "bad_image_id",
            f"image_id must be a plain *.png file name, got {name!r}",
        )
    return ImageContract.from_png(data_dir / name)


def _as_labeling(raw: Any, spec) -> np.ndarray:
    arr = np.asarray(raw)
    if arr.shape != (spec.height, spec.width):
        raise InputValidationError(
            "bad_labeling_shape",
            f"labeling must have shape {(spec.height, spec.width)}, "
            f"got {arr.shape}",
        )
    return arr


app = create_app()
