"""FastAPI application: categorized validation endpoints for the digest service."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import ENZYME_CATALOG_VERSION, MASS_TABLE_VERSION, __version__
from app.api.schemas import DigestRequest
from app.config import settings
from app.domain.mass import mass_table_metadata
from app.errors import DigestError
from app.logging_setup import configure_logging
from app.services.digest_service import DigestService

configure_logging(settings.log_dir, settings.log_level)

app = FastAPI(
    title="Synthetic Protein Digest Enumeration Service",
    version=__version__,
    description=(
        "Rule-based enzymatic digest fragment enumeration with explicit "
        "cleavage/blocking context, missed-cleavage enumeration, positional "
        "provenance, and mature monoisotopic mass tables."
    ),
)

service = DigestService(settings)


@app.exception_handler(DigestError)
async def digest_error_handler(_request: Request, exc: DigestError) -> JSONResponse:
    # Categorized failure: structured body, appropriate status, never 200/success.
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Schema-level failures (missing/wrongly-typed fields, malformed JSON) are
    # categorized explicitly instead of leaking FastAPI's default shape.
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "request body failed schema validation",
                "details": {"errors": exc.errors()},
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Unknown internal state is reported as a failure, not flattened to success.
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "unexpected internal error",
                "details": {"type": type(exc).__name__},
            },
        },
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/v1/meta", tags=["meta"])
def meta() -> dict:
    return {
        "versions": {
            "app_version": __version__,
            "mass_table_version": MASS_TABLE_VERSION,
            "enzyme_catalog_version": ENZYME_CATALOG_VERSION,
        },
        "mass_table": mass_table_metadata(),
        "constraints": {
            "max_sequence_length": settings.max_sequence_length,
            "max_missed_cleavages": settings.max_missed_cleavages,
            "mass_decimals": settings.mass_decimals,
        },
    }


@app.get("/api/v1/enzymes", tags=["catalog"])
def list_enzymes() -> dict:
    return {"success": True, "count": len(service.enzymes), "enzymes": service.list_enzymes()}


@app.post("/api/v1/digest", tags=["digest"])
def run_digest(request: DigestRequest) -> dict:
    return service.run_digest(
        raw_sequence=request.sequence,
        enzyme_name=request.enzyme,
        missed_cleavages=request.missed_cleavages,
        run_id=request.run_id,
    )


@app.get("/api/v1/runs", tags=["provenance"])
def list_runs(limit: int = 50) -> dict:
    limit = max(1, min(limit, 500))
    return {"success": True, "runs": service.list_runs(limit=limit)}


@app.get("/api/v1/runs/{run_id}", tags=["provenance"])
def get_run(run_id: str) -> dict:
    return {"success": True, "run": service.get_run(run_id)}
