"""Application service: one run from raw input to persisted evidence.

All cross-cutting concerns live here rather than in the kernel:

* run-id lifecycle and state transitions
* structured logging that preserves the run id, key intermediate state
  (per-fold counts, propensity range, estimate, SE) and the judgement reason
* mapping every failure category to a distinct, greppable log marker
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .config import AipwConfig, load_config
from .contract import Dataset, validate_dataset
from .diagnostics import build_evidence, new_run_id
from .errors import AipwError
from .kernel import estimate_aipw
from .repository import RunRepository


def get_logger() -> logging.Logger:
    logger = logging.getLogger("aipw")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(marker)s run_id=%(run_id)s %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


class RunService:
    def __init__(
        self,
        repository: RunRepository,
        config: AipwConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repo = repository
        self.config = config
        self.log = logger or get_logger()

    def _emit(self, level: int, marker: str, run_id: str, message: str, **extra: Any) -> None:
        payload = message
        if extra:
            payload = f"{message} {json.dumps(extra, sort_keys=True, default=float)}"
        self.log.log(level, payload, extra={"marker": marker, "run_id": run_id})

    def run(
        self,
        x: Any,
        a: Any,
        y: Any,
        cluster: Any | None = None,
        *,
        known_effect: float | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or new_run_id()
        request_snapshot = {
            "config": _config_snapshot(self.config),
            "known_effect": known_effect,
        }
        # create() can raise StateConflictError on run_id reuse; that happens
        # before any work starts, so it is logged separately.
        self.repo.create(run_id, request_snapshot)
        self._emit(logging.INFO, "RUN_QUEUED", run_id, "run created")
        self.repo.mark_running(run_id)
        self._emit(logging.INFO, "RUN_STARTED", run_id, "validating input")
        try:
            data: Dataset = validate_dataset(
                x,
                a,
                y,
                cluster,
                max_feature_cells=self.config.max_feature_cells,
                n_splits=self.config.folds.n_splits,
                estimand=self.config.estimand,
            )
            self._emit(
                logging.INFO,
                "INPUT_ACCEPTED",
                run_id,
                "input validated",
                n=data.n,
                p=data.p,
                n_treated=int((data.a == 1).sum()),
                n_control=int((data.a == 0).sum()),
                n_clusters=(
                    int(np.unique(data.cluster).shape[0])
                    if data.cluster is not None
                    else None
                ),
            )
            result = estimate_aipw(data, self.config)
            self._emit(
                logging.INFO,
                "CROSSFIT_DONE",
                run_id,
                "cross-fitting finished",
                estimate=result.estimate,
                se=result.se,
                pscore_min=float(result.pscore.min()),
                pscore_max=float(result.pscore.max()),
                folds=len(result.diagnostics),
                independent_unit=(
                    "cluster" if result.n_clusters else "individual"
                ),
            )
            evidence = build_evidence(
                result,
                data.a.astype(float),
                data.y,
                config_snapshot=request_snapshot["config"],
                known_effect=known_effect,
            )
            self.repo.mark_succeeded(run_id, evidence)
            verdict = (
                f"ci_covers_known={evidence['ci_covers_known']}"
                if known_effect is not None
                else f"ci_covers_zero={evidence['ci_covers_zero']}"
            )
            self._emit(
                logging.INFO,
                "RUN_SUCCEEDED",
                run_id,
                f"estimate={result.estimate:.6f} se={result.se:.6f} {verdict}",
                ci_low=result.ci_low,
                ci_high=result.ci_high,
            )
            evidence["run_id"] = run_id
            return evidence
        except AipwError as exc:
            self._record_failure(run_id, exc)
            raise

    def _record_failure(self, run_id: str, exc: AipwError) -> None:
        markers = {
            "input_error": "INPUT_REJECTED",
            "state_conflict": "STATE_CONFLICT",
            "resource_exhausted": "RESOURCE_EXHAUSTED",
            "computation_failure": "COMPUTATION_FAILED",
        }
        marker = markers.get(exc.category, "RUN_FAILED")
        self._emit(
            logging.ERROR,
            marker,
            run_id,
            f"{exc.category}: {exc.message}",
            **{"details": exc.details} if exc.details else {},
        )
        self.repo.mark_failed(run_id, exc.category, exc.message, exc.details)
        self._emit(logging.INFO, "RUN_FAILED_CLOSED", run_id, "run marked failed")


def _config_snapshot(config: AipwConfig) -> dict[str, Any]:
    return asdict(config)


def build_service(
    db_path: str | Path, config_path: str | Path | None = None
) -> tuple[RunService, RunRepository]:
    """Factory used by the API and experiment entry points."""
    config = load_config(config_path) if config_path else AipwConfig()
    repo = RunRepository(db_path)
    return RunService(repo, config), repo
