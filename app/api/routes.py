"""Thin HTTP routers. Persistence and behavior live in services."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import __version__
from app.api.deps import settings_dependency, store_dependency
from app.api.schemas import (
    DigestRequest,
    DigestResponse,
    RunSummaryView,
    ValidationRequest,
    ValidationResponse,
)
from app.config import Settings
from app.domain.constants import ENZYMES, MODIFICATIONS
from app.domain.errors import DigestError, ErrorCode
from app.logging_config import get_logger
from app.services.orchestrator import run_digest
from app.services.validation import validate_expectations
from app.storage.store import DigestStore

router = APIRouter()


_HTTP_STATUS = {
    ErrorCode.EMPTY_SEQUENCE: 422,
    ErrorCode.SEQUENCE_TOO_LONG: 422,
    ErrorCode.UNSUPPORTED_RESIDUE: 422,
    ErrorCode.INVALID_TERMINAL_MOD: 422,
    ErrorCode.INVALID_VARIABLE_MOD: 422,
    ErrorCode.MOD_FORM_LIMIT: 422,
    ErrorCode.INVALID_CHARGE: 422,
    ErrorCode.INVALID_MISSED_CLEAVAGES: 422,
    ErrorCode.INVALID_ENZYME: 422,
    ErrorCode.INVALID_CUSTOM_RULE: 422,
    ErrorCode.MOD_TARGET_UNKNOWN_RESIDUE: 422,
    ErrorCode.RUN_NOT_FOUND: 404,
    ErrorCode.VALIDATION_NOT_PENDING: 409,
    ErrorCode.DIGEST_FAILED: 500,
    ErrorCode.MASS_UNCERTAIN: 200,
    ErrorCode.STORAGE_ERROR: 500,
}


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service_version": __version__}


@router.get("/enzymes")
def list_enzymes() -> dict:
    return {
        "service_version": __version__,
        "enzymes": [rule.to_echo() for rule in ENZYMES.values()],
        "modifications": [
            {
                "key": mod.key,
                "name": mod.name,
                "delta": mod.delta,
                "targets": "".join(sorted(mod.targets)),
                "terminus": mod.terminus,
                "default_kind": mod.default_kind,
            }
            for mod in MODIFICATIONS.values()
        ],
    }


@router.post("/digest", response_model=DigestResponse)
def digest_endpoint(
    request: DigestRequest,
    settings: Settings = Depends(settings_dependency),
    store: DigestStore = Depends(store_dependency),
) -> dict:
    return run_digest(request, settings=settings, store=store)


@router.post("/validate", response_model=ValidationResponse)
def validate_endpoint(
    request: ValidationRequest,
    settings: Settings = Depends(settings_dependency),
    store: DigestStore = Depends(store_dependency),
) -> dict:
    if request.run_id is not None:
        # Validation in this implementation runs the authored expectations
        # directly; a run_id-only re-check is reported distinctly rather than
        # silently fabricating expectations.
        existing = store.get_run(request.run_id)
        if existing is None:
            raise DigestError(
                ErrorCode.RUN_NOT_FOUND,
                f"run {request.run_id!r} not found",
            )
    return validate_expectations(
        request.expectations, settings=settings, store=store
    )


@router.get("/runs")
def list_runs(
    limit: int = 50,
    store: DigestStore = Depends(store_dependency),
) -> dict:
    limit = max(1, min(limit, 200))
    return {"service_version": __version__, "runs": store.list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    store: DigestStore = Depends(store_dependency),
) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise DigestError(ErrorCode.RUN_NOT_FOUND, f"run {run_id!r} not found")
    return {"service_version": __version__, "run": run}


def register_error_handlers(app) -> None:  # noqa: ANN001
    logger = get_logger()

    @app.exception_handler(DigestError)
    async def handle_digest_error(_: Request, exc: DigestError) -> JSONResponse:
        logger.error(
            "domain error",
            extra={
                "extra_fields": {
                    "event": "domain_error",
                    "error": exc.code.value,
                    "position": exc.position,
                    "verdict": "ERROR",
                }
            },
        )
        status_code = _HTTP_STATUS.get(exc.code, 400)
        return JSONResponse(status_code=status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.error(
            "request validation failed",
            extra={
                "extra_fields": {
                    "event": "request_validation_error",
                    "errors": exc.errors(),
                    "verdict": "ERROR",
                }
            },
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": "REQUEST_VALIDATION_ERROR",
                "message": "request payload failed schema validation",
                "detail": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled error",
            extra={"extra_fields": {"event": "unhandled_error", "verdict": "ERROR"}},
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "unexpected internal error",
            },
        )
