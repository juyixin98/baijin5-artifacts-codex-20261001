"""HTTP API layer: validation, synchronous segmentation, chunked jobs."""

from __future__ import annotations

import hashlib
import json
import uuid

import numpy as np
from fastapi import APIRouter, Body, HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.contracts import (
    ContractIssue,
    ImageSegmentationRequest,
    SegmentationRequest,
    SegmentationResponse,
    ValidationReport,
    collect_request_issues,
    decode_png_gray,
)
from app.core.gradient import gradient_magnitude
from app.core.watershed import WatershedInputError, flood_watershed
from app.jobs.manager import JobManager, JobStatus
from app.logging_setup import get_logger, log_event


def _input_digest(payload: dict) -> str:
    """Stable hash of the request payload, so logs can be tied to the input."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_pixel_cap(n_pixels: int, settings: Settings) -> None:
    if n_pixels > settings.max_image_pixels:
        raise HTTPException(
            status_code=413,
            detail={
                "category": "payload_too_large",
                "message": f"{n_pixels} pixels exceeds the configured limit "
                           f"of {settings.max_image_pixels}",
            },
        )


def _raise_first_issue(issues) -> None:
    """Fail a mutating endpoint with a categorised 422 on contract violations."""
    if issues:
        first = issues[0]
        raise HTTPException(
            status_code=422,
            detail={
                "category": first.category,
                "message": first.message,
                "issues": [issue.model_dump() for issue in issues],
            },
        )


def create_router(settings: Settings, job_manager: JobManager, versions: dict[str, str]) -> APIRouter:
    router = APIRouter()
    logger = get_logger()

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok", "versions": versions}

    @router.post("/v1/validate", response_model=ValidationReport)
    def validate(payload: dict = Body(...)) -> ValidationReport:
        """Dry-run contract check: reports every issue, never runs the flood."""
        try:
            request = SegmentationRequest(**payload)
        except ValidationError as exc:
            issues = [
                ContractIssue(
                    category="schema_violation",
                    message=".".join(str(part) for part in error["loc"]) + ": " + error["msg"],
                    location=".".join(str(part) for part in error["loc"]),
                )
                for error in exc.errors()
            ]
            return ValidationReport(valid=False, issues=issues)
        issues = collect_request_issues(request)
        return ValidationReport(valid=not issues, issues=issues)

    @router.post("/v1/segment", response_model=SegmentationResponse)
    def segment(request: SegmentationRequest) -> SegmentationResponse:
        _raise_first_issue(collect_request_issues(request))
        run_id = uuid.uuid4().hex
        digest = _input_digest(request.model_dump())
        elevation = np.asarray(request.elevation, dtype=np.float64)
        markers = np.asarray(request.markers, dtype=np.int64)
        mask = np.asarray(request.mask, dtype=bool) if request.mask is not None else None
        _check_pixel_cap(int(elevation.size), settings)
        log_event(
            logger, 20, "run_started",
            run_id=run_id, input_sha256=digest, shape=list(elevation.shape),
            connectivity=request.connectivity, chunk_size=request.chunk_size,
            seed_count=int((markers > 0).sum()), versions=versions,
        )
        try:
            result = flood_watershed(
                elevation, markers,
                connectivity=request.connectivity, mask=mask,
                chunk_size=request.chunk_size,
            )
        except WatershedInputError as exc:
            log_event(
                logger, 40, "run_failed",
                run_id=run_id, input_sha256=digest,
                error_category=exc.category, error_message=exc.message,
            )
            raise HTTPException(
                status_code=422,
                detail={"category": exc.category, "message": exc.message, "run_id": run_id},
            ) from exc
        log_event(
            logger, 20, "run_finished",
            run_id=run_id, input_sha256=digest, stats=result.stats.as_dict(),
        )
        return SegmentationResponse(
            run_id=run_id,
            input_sha256=digest,
            labels=result.labels.tolist(),
            boundary=result.boundary.tolist(),
            stats=result.stats.as_dict(),
            versions=versions,
        )

    @router.post("/v1/segment/image", response_model=SegmentationResponse)
    def segment_image(request: ImageSegmentationRequest) -> SegmentationResponse:
        """Segment a grayscale PNG: elevation = gradient magnitude of the image."""
        try:
            image = decode_png_gray(request.elevation_png_b64, "elevation_png_b64")
            markers_png = decode_png_gray(request.markers_png_b64, "markers_png_b64")
        except ValueError as exc:
            category, _, message = str(exc).partition(": ")
            raise HTTPException(
                status_code=422, detail={"category": category, "message": message}
            ) from exc
        if image.shape != markers_png.shape:
            raise HTTPException(
                status_code=422,
                detail={
                    "category": "shape_mismatch",
                    "message": f"image shape {image.shape} != markers shape {markers_png.shape}",
                },
            )
        _check_pixel_cap(int(image.size), settings)
        elevation = gradient_magnitude(image, sigma=request.smooth_sigma)
        markers = markers_png.astype(np.int64)
        sync_request = SegmentationRequest(
            elevation=elevation.tolist(),
            markers=markers.tolist(),
            connectivity=request.connectivity,
        )
        return segment(sync_request)

    @router.post("/v1/jobs", status_code=202)
    def submit_job(request: SegmentationRequest) -> dict:
        _raise_first_issue(collect_request_issues(request))
        elevation = np.asarray(request.elevation, dtype=np.float64)
        markers = np.asarray(request.markers, dtype=np.int64)
        mask = np.asarray(request.mask, dtype=bool) if request.mask is not None else None
        _check_pixel_cap(int(elevation.size), settings)
        digest = _input_digest(request.model_dump())
        record = job_manager.submit(
            elevation, markers,
            connectivity=request.connectivity, mask=mask,
            chunk_size=request.chunk_size, input_sha256=digest,
        )
        return record.public_view()

    @router.get("/v1/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        record = job_manager.get(job_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"category": "job_not_found", "message": f"no job with id {job_id}"},
            )
        return record.public_view()

    @router.get("/v1/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict:
        record = job_manager.get(job_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"category": "job_not_found", "message": f"no job with id {job_id}"},
            )
        if record.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise HTTPException(
                status_code=409,
                detail={"category": "job_not_finished", "message": f"job {job_id} is {record.status.value}"},
            )
        if record.status is JobStatus.FAILED:
            raise HTTPException(
                status_code=422,
                detail={
                    "category": record.error_category,
                    "message": record.error_message,
                    "run_id": record.run_id,
                },
            )
        return {
            "status": "succeeded",
            "job_id": record.job_id,
            "run_id": record.run_id,
            "input_sha256": record.input_sha256,
            "labels": record.labels.tolist(),
            "boundary": record.boundary.tolist(),
            "stats": record.stats,
            "versions": versions,
        }

    return router
