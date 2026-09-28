"""FastAPI backend.

Error taxonomy -> HTTP status
-----------------------------
input_error            400 BAD_REQUEST      (malformed/invalid payload)
state_conflict         409 CONFLICT         (run id reuse, bad transition)
resource_exhausted     507 INSUFFICIENT_STORAGE (memory/cell budget)
computation_failed     422 UNPROCESSABLE    (numerical kernel failure)

Every response uses the envelope ``{success, data|error}``. Runs execute
synchronously (the workloads here are small) but their full lifecycle is
persisted in SQLite and is replayable via ``/runs/{id}/events``.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .contract import (
    AipwError, Config, ErrorCategory, InputError, ResourceExhaustedError,
)
from .crossfit import cross_fit
from . import diagnostics as diag
from .jobs import JobStore
from .pipeline import assign_folds, run_estimate, validate_dataset

DEFAULT_MAX_ROWS = 200_000
DEFAULT_MAX_CELLS = 20_000_000


class DataPayload(BaseModel):
    x: list[list[float]]
    a: list[float]
    y: list[float]
    cluster_id: list[Any] | None = None


class EstimateRequest(BaseModel):
    data: DataPayload
    config: dict[str, Any] | None = None
    seed: int = 0
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,128}$")


class VerifyRequest(BaseModel):
    data: DataPayload


def _error_response(exc: AipwError) -> JSONResponse:
    status = {
        ErrorCategory.INPUT: 400,
        ErrorCategory.STATE: 409,
        ErrorCategory.RESOURCE: 507,
        ErrorCategory.COMPUTATION: 422,
    }[exc.category]
    return JSONResponse(status_code=status,
                        content={"success": False, "error": exc.to_dict()})


def _to_payload(data: DataPayload) -> dict[str, Any]:
    return {"x": data.x, "a": data.a, "y": data.y,
            "cluster_id": data.cluster_id}


def _config_for_data(config: Config, dataset) -> Config:
    """Cluster ids in the payload opt cluster-robust inference on.

    Supplying cluster ids while leaving clustering disabled is almost always a
    mistake (naive SEs are then wrong), so presence of ids turns it on; the
    effective config is echoed back in the result.
    """
    if dataset.clusters is not None and not config.cluster.enabled:
        from dataclasses import replace
        return replace(config,
                       cluster=replace(config.cluster, enabled=True))
    if config.cluster.enabled and dataset.clusters is None:
        raise InputError(
            "cluster inference requested but no cluster_id column was supplied")
    return config


def create_app(store_path: str | Path | None = None) -> FastAPI:
    if store_path is None:
        # default is an in-memory registry; set AIPW_DB to persist runs to a
        # file (the documented uvicorn invocation does this)
        store_path = os.environ.get("AIPW_DB", ":memory:")
    store = JobStore(store_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        store.close()

    app = FastAPI(title="AIPW estimation backend", version="1.0.0",
                  lifespan=lifespan)
    app.state.store = store
    app.state.max_rows = int(os.environ.get("AIPW_MAX_ROWS", DEFAULT_MAX_ROWS))
    app.state.max_cells = int(os.environ.get("AIPW_MAX_CELLS", DEFAULT_MAX_CELLS))

    def _guard_resources(n: int, p: int) -> None:
        if n > app.state.max_rows:
            raise ResourceExhaustedError(
                "dataset exceeds the row budget",
                details={"rows": n, "budget": app.state.max_rows},
            )
        cells = n * p
        if cells > app.state.max_cells:
            raise ResourceExhaustedError(
                "covariate matrix exceeds the cell budget",
                details={"cells": cells, "budget": app.state.max_cells},
            )

    @app.exception_handler(AipwError)
    async def _aipw_error_handler(_request, exc: AipwError):
        return _error_response(exc)

    @app.get("/health")
    def health():
        return {"success": True, "data": {"status": "ok"}}

    @app.post("/estimate")
    def estimate_endpoint(req: EstimateRequest):
        store: JobStore = app.state.store
        run_id = store.create_run(seed=req.seed, run_id=req.run_id)
        try:
            config = Config.from_dict(req.config or {})
            dataset = validate_dataset(_to_payload(req.data))
            config = _config_for_data(config, dataset)
            _guard_resources(dataset.n, dataset.p)
            store.mark_running(run_id, {
                "n": dataset.n, "p": dataset.p, "folds": config.folds,
                "estimand": config.estimand.value,
                "clustered": config.cluster.enabled,
                "stabilized": config.stabilize_weight,
            })
            try:
                result = run_estimate(dataset, config, seed=req.seed,
                                      run_id=run_id)
            except MemoryError as exc:
                raise ResourceExhaustedError(
                    "out of memory while fitting nuisance models") from exc
            store.mark_succeeded(run_id, result.to_dict())
        except AipwError as exc:
            store.mark_failed(run_id, exc.category.value, exc.message, exc.details)
            return _error_response(exc)
        except Exception as exc:  # defensive: never leak a raw 500
            failure = ResourceExhaustedError if isinstance(exc, MemoryError) else None
            if failure is not None:
                err = failure(str(exc))
            else:
                from .contract import ComputationFailure
                err = ComputationFailure(
                    f"unexpected kernel failure: {type(exc).__name__}: {exc}")
            store.mark_failed(run_id, err.category.value, err.message, err.details)
            return _error_response(err)

        return {"success": True, "data": result.to_dict(), "run_id": run_id}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        return {"success": True, "data": app.state.store.get(run_id)}

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str):
        return {"success": True, "data": {"run_id": run_id,
                "events": app.state.store.events(run_id)}}

    @app.post("/runs/{run_id}/verify")
    def verify_run(run_id: str, req: VerifyRequest):
        """Replay a run independently and audit leakage / OOF alignment.

        The fold split is regenerated deterministically from the stored seed
        and the supplied data; results must reproduce the stored point, the
        OOF predictions must match an independent refit, per-fold scalers must
        match train-only statistics, and component estimators are summarized
        with their (non-)agreement.
        """
        store: JobStore = app.state.store
        record = store.get(run_id)
        if record["state"] != "succeeded" or record["result"] is None:
            raise InputError("can only verify a succeeded run",
                             details={"state": record["state"]})
        config = Config.from_dict(record["result"]["config"])
        dataset = validate_dataset(_to_payload(req.data))
        config = _config_for_data(config, dataset)
        seed = int(record["seed"])
        fold_id = assign_folds(dataset, config, np.random.default_rng(seed))
        oof = cross_fit(dataset, config, fold_id)
        refit = diag.verify_oof_predictions(dataset, config, fold_id, oof)
        leakage = diag.audit_scaler_stats(
            dataset, fold_id, config.folds, oof,
            standardize=config.outcome_models.standardize)

        replay = run_estimate(dataset, config, seed=seed, run_id=run_id,
                              fold_id=fold_id)
        stored_point = float(record["result"]["point"])
        replay_diff = abs(replay.point - stored_point)
        reproduced = replay_diff <= 1e-10

        agreement = diag.estimator_agreement(
            replay.gcomp_point, replay.ipw_point, replay.aipw_point)
        return {"success": True, "data": {
            "run_id": run_id,
            "reproduced": reproduced,
            "replay_point_diff": replay_diff,
            "oof_refit": refit.to_dict(),
            "leakage_audit": leakage.to_dict(),
            "agreement": agreement.to_dict(),
        }}

    return app


# Module-level ASGI app for ``uvicorn aipw.api:app``. It is in-memory unless
# AIPW_DB is set, so merely importing the package never creates files.
app = create_app(os.environ.get("AIPW_DB", ":memory:"))
