"""Replication experiments over known-effect synthetic data.

Scenarios (the acceptance fixtures, exercised at scale):

    both_correct    logistic propensity + linear outcome
    propensity_only correct propensity, constant (wrong) outcome
    outcome_only   constant (wrong) propensity, correct outcome
    both_wrong     constant propensity + constant outcome

Expected facts, checked both per-replication in the test suite and in
aggregate here:

* first three scenarios are centred on the known tau (double robustness);
* ``both_wrong`` is biased by an amount matching the unadjusted mean
  difference, and its tau-coverage collapses;
* with clustered residuals, influence-function SE computed treating rows as
  independent under-covers, while the cluster-summed SE covers at the nominal
  rate.

Every replication is executed through :class:`RunService`, so each gets a
persisted run id, structured log line and evidence row - the experiment is
replayable from SQLite, not a detached simulation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import AipwConfig, FoldConfig, ModelConfig
from .dgp import generate_sample
from .repository import RunRepository
from .service import RunService

SCENARIOS = ("both_correct", "propensity_only", "outcome_only", "both_wrong")

_LOGISTIC = ModelConfig("logistic_ridge", penalty=1e-6)
_OLS = ModelConfig("ols_ridge", penalty=1e-6)
_CONST = ModelConfig("wrong_constant", penalty=1e-6)

_SCENARIO_CONFIG = {
    "both_correct": (_LOGISTIC, _OLS),
    "propensity_only": (_LOGISTIC, _CONST),
    "outcome_only": (_CONST, _OLS),
    "both_wrong": (_CONST, _CONST),
}


def scenario_config(name: str, folds: FoldConfig) -> AipwConfig:
    if name not in _SCENARIO_CONFIG:
        raise KeyError(f"unknown scenario {name!r}")
    g, m = _SCENARIO_CONFIG[name]
    return AipwConfig(
        folds=folds, treatment_model=g, outcome_model=m, estimand="ATE"
    )


def _aggregate(
    estimates: np.ndarray,
    ses: np.ndarray,
    covers: np.ndarray,
    tau: float,
) -> dict[str, float]:
    z975 = 1.959963984540054
    return {
        "replications": int(estimates.shape[0]),
        "mean_estimate": float(np.mean(estimates)),
        "bias": float(np.mean(estimates) - tau),
        "empirical_se": float(np.std(estimates, ddof=1)),
        "mean_analytic_se": float(np.mean(ses)),
        "se_ratio_analytic_over_empirical": float(
            np.mean(ses) / np.std(estimates, ddof=1)
        ),
        "coverage_95": float(np.mean(covers)),
        "ci_halfwidth_95": float(z975 * float(np.mean(ses))),
    }


def run_scenario(
    name: str,
    service: RunService,
    *,
    replications: int,
    n: int,
    tau: float,
    base_seed: int,
    folds: FoldConfig,
    clustered: bool = False,
    n_clusters: int | None = None,
    cluster_size: int | None = None,
    icc: float = 0.0,
    cluster_randomized: bool = False,
    run_id_prefix: str = "exp",
) -> dict[str, Any]:
    """Run one scenario; optionally repeat each replication twice in the
    clustered case (with and without cluster ids) to compare SE coverage."""
    service.config = scenario_config(name, folds)
    estimates = np.empty(replications)
    ses = np.empty(replications)
    covers = np.empty(replications, dtype=bool)
    iid_ses: np.ndarray | None = (
        np.empty(replications) if clustered else None
    )
    run_ids: list[str] = []

    for r in range(replications):
        seed = base_seed + r
        sample = generate_sample(
            n,
            seed,
            tau=tau,
            n_clusters=n_clusters if clustered else None,
            cluster_size=cluster_size if clustered else None,
            icc=icc,
            cluster_randomized=cluster_randomized,
        )
        rid = f"{run_id_prefix}-{name}-{r:04d}-{seed}"
        evidence = service.run(
            sample.x,
            sample.a,
            sample.y,
            sample.cluster,
            known_effect=tau,
            run_id=rid,
        )
        run_ids.append(rid)
        estimates[r] = evidence["estimate"]
        ses[r] = evidence["se"]
        covers[r] = evidence["ci_covers_known"]

        if clustered:
            # Same data, same code path, but rows treated as independent.
            ev_iid = service.run(
                sample.x,
                sample.a,
                sample.y,
                None,
                known_effect=tau,
                run_id=f"{rid}-iid",
            )
            iid_ses[r] = ev_iid["se"]

    summary: dict[str, Any] = {
        "scenario": name,
        "clustered": clustered,
        "summary": _aggregate(estimates, ses, covers, tau),
        "first_run_ids": run_ids[:5],
        "last_run_id": run_ids[-1],
    }
    if clustered:
        lo = estimates - 1.959963984540054 * iid_ses
        hi = estimates + 1.959963984540054 * iid_ses
        summary["iid_rows_as_independent"] = _aggregate(
            estimates, iid_ses, (lo <= tau) & (tau <= hi), tau
        )
        summary["cluster_units"] = summary["summary"]
    return summary


def run_experiment(
    config: dict[str, Any],
    db_path: str | Path,
    *,
    scenarios: tuple[str, ...] = SCENARIOS,
    log_silent: bool = True,
) -> dict[str, Any]:
    """Top-level entry point used by ``scripts/run_experiment.py``."""
    folds = FoldConfig(**config["folds"])
    tau = float(config["tau"])
    n = int(config["n"])
    reps = int(config["replications"])
    base_seed = int(config["seed"])

    import logging

    logger = logging.getLogger(f"aipw.experiment.{time.strftime('%H%M%S')}")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL if log_silent else logging.INFO)

    repo = RunRepository(db_path)
    service = RunService(repo, scenario_config(scenarios[0], folds), logger=logger)
    results: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parameters": {
            "replications": reps,
            "n": n,
            "tau": tau,
            "base_seed": base_seed,
            "folds": config["folds"],
        },
        "iid_scenarios": {},
        "cluster_experiment": None,
    }
    try:
        for name in scenarios:
            results["iid_scenarios"][name] = run_scenario(
                name,
                service,
                replications=reps,
                n=n,
                tau=tau,
                base_seed=base_seed,
                folds=folds,
                run_id_prefix="exp-iid",
            )
        cc = config["cluster"]
        c_folds = FoldConfig(**{**config["folds"], "seed": cc["seed"]})
        results["cluster_experiment"] = run_scenario(
            "both_correct",
            service,
            replications=int(cc["replications"]),
            n=int(cc["n_clusters"]) * int(cc["cluster_size"]),
            tau=tau,
            base_seed=cc["seed"],
            folds=c_folds,
            clustered=True,
            n_clusters=int(cc["n_clusters"]),
            cluster_size=int(cc["cluster_size"]),
            icc=float(cc["icc"]),
            cluster_randomized=bool(cc.get("cluster_randomized", True)),
            run_id_prefix="exp-clu",
        )
    finally:
        repo.close()
    return results


def main(config_path: str, db_path: str, out_path: str) -> None:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    results = run_experiment(config, db_path)
    Path(out_path).write_text(
        json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, sort_keys=True))
