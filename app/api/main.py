"""FastAPI application exposing the local-linear RD estimator."""
from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .. import __version__
from ..core.estimator import rd_estimate
from ..logging_setup import configure_logging, log_event
from ..storage import RunStore
from .schemas import RDEstimateRequest

app = FastAPI(
    title="Local-linear RD backend",
    version=__version__,
    description=(
        "Sharp regression-discontinuity estimation with side-specific "
        "local-linear fits, reproducible kernel/bandwidth choices, and "
        "discreteness/heaping/density diagnostics."
    ),
)

logger = configure_logging()
_store: RunStore | None = None


def store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.post("/api/v1/rd/estimate")
def estimate(request: RDEstimateRequest) -> JSONResponse:
    if len(request.x) != len(request.y):
        raise HTTPException(
            status_code=422,
            detail={"error": "x and y must have equal length",
                    "failure_category": "bad_input"},
        )

    log_event(logger, logging.INFO, "rd estimate request",
              run_id=request.run_id, n=len(request.x), cutoff=request.cutoff,
              kernel=request.kernel, bandwidth=str(request.bandwidth),
              step="received")

    result = rd_estimate(
        np.asarray(request.x, dtype=np.float64),
        np.asarray(request.y, dtype=np.float64),
        cutoff=request.cutoff,
        kernel=request.kernel,
        bandwidth=request.bandwidth,
        se_type=request.se_type,
        run_id=request.run_id,
        alpha=request.alpha,
    )
    payload = result.to_dict()
    store().save_run(request.model_dump(mode="json"), payload)

    # Explicit, distinguishable terminal states — never "success by default".
    level = {
        "success": logging.INFO,
        "warning": logging.WARNING,
        "failed": logging.ERROR,
    }[result.status.value]
    log_event(
        logger, level, "rd estimate complete",
        run_id=result.run_id, status=result.status.value,
        tau=result.tau, se=result.se,
        failure_category=result.failure_category.value
        if result.failure_category else None,
        failure_reason=result.failure_reason,
        n_warnings=len(result.warnings),
        versions=result.versions,
        step="completed",
    )
    # Failed estimations are 422 (the request is well-formed but statistically
    # unidentifiable), keeping the structured body rather than a bare 500.
    status_code = 200 if result.status.value != "failed" else 422
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    run = store().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "run not found"})
    return run


@app.get("/api/v1/runs")
def list_runs(limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(limit, 500))
    return {"runs": store().list_runs(limit)}
