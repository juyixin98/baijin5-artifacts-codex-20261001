"""Service layer: orchestrates storage, array assembly and estimation.

This is the only place that knows how stored rows become estimator inputs and
how a completed or failed run becomes a persisted record.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from app.core import estimator as estimator_mod
from app.core.config import AppConfig
from app.core.contracts import (
    CovariateDeclaration,
    MissingStrategy,
    ThetaSource,
    ZeroVarianceStrategy,
)
from app.core.errors import ErrorCode, EstimationError, ValidationError
from app.core.logging_setup import get_logger, new_run_id
from app.storage.db import Database


class ExperimentService:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config

    def register(self, experiment_id: str, description: str, declarations: list[dict[str, Any]]) -> None:
        names = [d["name"] for d in declarations]
        if len(names) != len(set(names)):
            raise ValidationError(
                ErrorCode.INVALID_REQUEST,
                "duplicate covariate names in declarations",
                {"names": names},
            )
        self.db.register_experiment(experiment_id, description, declarations)

    def upload(self, experiment_id: str, observations: list[dict[str, Any]]) -> int:
        row = self.db.require_experiment(experiment_id)
        declared = {d["name"] for d in json.loads(row["declarations"])}
        prepared = []
        for obs in observations:
            cov = obs.get("covariates", {}) or {}
            undeclared = set(cov) - declared
            if undeclared:
                raise ValidationError(
                    ErrorCode.UNDECLARED_COVARIATE,
                    "observation carries a covariate absent from declarations",
                    {"unit_id": obs.get("unit_id"), "covariates": sorted(undeclared)},
                )
            missing_cols = declared - set(cov)
            if missing_cols:
                cov = {**cov, **{name: None for name in missing_cols}}
            full_cov: dict[str, float | None] = {name: cov.get(name) for name in declared}
            prepared.append((obs["unit_id"], int(obs["treatment"]), float(obs["outcome"]), full_cov))
        return self.db.insert_observations(experiment_id, prepared)

    def run_estimation(
        self,
        experiment_id: str,
        *,
        covariate_names: list[str] | None = None,
        missing_strategy: str = "mean_impute",
        zero_variance_strategy: str = "drop",
        theta_source: str = "control",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or new_run_id()
        logger = get_logger("service", run_id)
        row = self.db.require_experiment(experiment_id)
        declarations_raw = json.loads(row["declarations"])

        try:
            selected = covariate_names if covariate_names is not None else [d["name"] for d in declarations_raw]
            unknown = [n for n in selected if n not in {d["name"] for d in declarations_raw}]
            if unknown:
                raise ValidationError(
                    ErrorCode.UNDECLARED_COVARIATE,
                    "requested covariate is not declared for this experiment",
                    {"covariates": unknown},
                )
            decl_by_name = {d["name"]: CovariateDeclaration(**d) for d in declarations_raw}
            declarations = [decl_by_name[n] for n in selected]

            records = self.db.load_observations(experiment_id)
            if not records:
                raise ValidationError(ErrorCode.EMPTY_DATA, "experiment has no observations", {})
            logger.info("assembling arrays | n_rows=%d run_id=%s", len(records), run_id)

            unit_id = np.array([r["unit_id"] for r in records])
            treatment = np.array([r["treatment"] for r in records], dtype=float)
            outcome = np.array([r["outcome"] for r in records], dtype=float)
            covariate_columns: dict[str, np.ndarray] = {}
            for name in selected:
                covariate_columns[name] = np.array(
                    [json.loads(r["covariates"]).get(name, np.nan) for r in records], dtype=float
                )

            result = estimator_mod.estimate(
                experiment_id=experiment_id,
                unit_id=unit_id,
                treatment=treatment,
                outcome=outcome,
                covariate_columns=covariate_columns,
                declarations=declarations,
                config=self.config.estimation,
                run_id=run_id,
                missing_strategy=MissingStrategy(missing_strategy),
                zero_variance_strategy=ZeroVarianceStrategy(zero_variance_strategy),
                theta_source=ThetaSource(theta_source),
            )
            payload = result.to_dict()
            self.db.save_run(run_id, experiment_id, payload)
            logger.info("run completed and persisted | run_id=%s", run_id)
            return payload

        except EstimationError as exc:
            logger.error("run failed | code=%s message=%s", exc.code.value, exc.message)
            failed = estimator_mod.failed_run(run_id, experiment_id, exc).to_dict()
            self.db.save_run(run_id, experiment_id, failed)
            raise

    def get_run(self, run_id: str) -> dict[str, Any]:
        payload = self.db.get_run(run_id)
        if payload is None:
            raise ValidationError(ErrorCode.UNKNOWN_RUN, f"run {run_id!r} not found", {"run_id": run_id})
        return payload
