"""HTTP routes for the phasing service."""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.errors import PhasingError
from app.logging_utils import log_step
from app.models import PhaseRequest
from app.parsing import parse_request
from app.phasing.pipeline import run_phasing
from app.version import ALGORITHM_VERSION, APP_VERSION

router = APIRouter()


def _config_snapshot(settings) -> dict:
    return {
        "max_enum_sites": settings.max_enum_sites,
        "default_quality": settings.default_quality,
        "max_quality": settings.max_quality,
        "max_reported_solutions": settings.max_reported_solutions,
    }


@router.get("/v1/health")
def health() -> dict:
    return {"status": "up"}


@router.get("/v1/version")
def version() -> dict:
    return {"app_version": APP_VERSION, "algorithm_version": ALGORITHM_VERSION}


@router.post("/v1/phase")
def phase(request: PhaseRequest, http_request: Request) -> JSONResponse:
    settings = http_request.app.state.settings
    store = http_request.app.state.store
    logger = http_request.app.state.logger

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    input_sha256 = hashlib.sha256(
        json.dumps(request.model_dump(), sort_keys=True).encode("utf-8")
    ).hexdigest()
    log_step(
        logger, request_id, "received",
        "phasing request received",
        sample=request.sample, num_variants=len(request.variants),
        num_reads=len(request.reads), input_sha256=input_sha256,
    )

    failure: dict | None = None
    try:
        parsed = parse_request(request, settings)
        log_step(
            logger, request_id, "parsed",
            "input parsed and validated",
            num_fragments=len(parsed.fragments),
            unknown_allele_observations=parsed.unknown_allele_observations,
        )
        result = run_phasing(parsed, settings)
        log_step(
            logger, request_id, "phased",
            "phasing completed",
            status=result["status"], num_blocks=result["summary"]["num_blocks"],
            total_mec_score=result["summary"]["total_mec_score"],
            uncertainties=result["uncertainties"],
        )
    except PhasingError as exc:
        log_step(
            logger, request_id, "failed",
            "phasing failed",
            failure_category=exc.category.value, detail=exc.detail,
        )
        failure = {"category": exc.category.value, "detail": exc.detail}
        result = {
            "status": "FAILED",
            "failure": failure,
            "uncertainties": [],
            "versions": {
                "app": APP_VERSION,
                "algorithm": ALGORITHM_VERSION,
                "config": _config_snapshot(settings),
            },
            "summary": None,
            "blocks": [],
        }

    result["request_id"] = request_id
    result["input_sha256"] = input_sha256
    store.record_run(
        request_id=request_id,
        app_version=APP_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        config=_config_snapshot(settings),
        input_sha256=input_sha256,
        status=result["status"],
        result=result,
        failure_category=failure["category"] if failure else None,
        failure_detail=failure["detail"] if failure else None,
    )
    log_step(logger, request_id, "recorded", "run recorded in provenance store")
    return JSONResponse(result)


@router.get("/v1/runs/{request_id}")
def get_run(request_id: str, http_request: Request) -> JSONResponse:
    store = http_request.app.state.store
    record = store.get_run(request_id)
    if record is None:
        return JSONResponse(
            status_code=404,
            content={
                "status": "FAILED",
                "failure": {
                    "category": "RUN_NOT_FOUND",
                    "detail": f"no run recorded with request_id {request_id!r}",
                },
            },
        )
    return JSONResponse(record)
