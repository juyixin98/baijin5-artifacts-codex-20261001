"""Replication experiment: aggregate coverage and bias, with replay via runs."""

from __future__ import annotations

import numpy as np

from aipw_backend.config import FoldConfig
from aipw_backend.experiment import run_experiment, run_scenario, scenario_config
from aipw_backend.repository import RunRepository
from aipw_backend.service import RunService

import logging

_SILENT = logging.getLogger("exp-test")
_SILENT.addHandler(logging.NullHandler())


def _service(db):
    folds = FoldConfig(n_splits=5, seed=4242)
    return RunService(db, scenario_config("both_correct", folds), logger=_SILENT), folds


def test_double_robust_scenarios_cover_known_effect(tmp_path):
    """The three doubly robust scenarios are unbiased and cover tau.

    SE calibration is scenario-specific for a principled reason:

    * both_correct / outcome_only: plug-in influence-function SE matches the
      empirical estimator SD (ratio ~ 1);
    * propensity_only (correct logistic pscore, constant outcome): the
      pscore MLE enforces in-sample covariate balance on the same X-space in
      which the true outcome regression is linear, projecting out that
      variation (the HIR / nuisance-projection efficiency gain). The plug-in
      IF SE then over-states the realized sampling variability - it is
      conservative (coverage >= nominal), never anti-conservative.
    """
    repo = RunRepository(tmp_path / "e.db")
    svc, folds = _service(repo)
    expected = {
        "both_correct": (0.08, 0.88, 0.80, 1.25),
        "outcome_only": (0.08, 0.88, 0.80, 1.25),
        "propensity_only": (0.08, 0.93, 1.10, 2.50),
    }
    for name, (bias_tol, cov_floor, ratio_lo, ratio_hi) in expected.items():
        summary = run_scenario(
            name, svc,
            replications=60, n=3000, tau=2.0, base_seed=700,
            folds=folds, run_id_prefix="t",
        )["summary"]
        assert abs(summary["bias"]) < bias_tol, (name, summary["bias"])
        assert summary["coverage_95"] > cov_floor, (
            name, summary["coverage_95"]
        )
        ratio = summary["se_ratio_analytic_over_empirical"]
        assert ratio_lo < ratio < ratio_hi, (name, ratio)
    repo.close()


def test_both_wrong_scenario_fails_to_cover_and_is_biased(tmp_path):
    repo = RunRepository(tmp_path / "e.db")
    svc, folds = _service(repo)
    summary = run_scenario(
        "both_wrong", svc,
        replications=80, n=3000, tau=2.0, base_seed=800,
        folds=folds, run_id_prefix="t",
    )["summary"]
    assert summary["bias"] > 0.20, summary
    assert summary["coverage_95"] < 0.60, summary
    repo.close()


def test_cluster_experiment_shows_iid_undercoverage(tmp_path):
    repo = RunRepository(tmp_path / "e.db")
    svc, folds = _service(repo)
    out = run_scenario(
        "both_correct", svc,
        replications=80, n=1500, tau=2.0, base_seed=900, folds=folds,
        clustered=True, n_clusters=150, cluster_size=10, icc=0.6,
        cluster_randomized=True,
        run_id_prefix="tc",
    )
    cluster_cov = out["cluster_units"]["coverage_95"]
    iid_cov = out["iid_rows_as_independent"]["coverage_95"]
    assert cluster_cov > 0.88, cluster_cov
    assert iid_cov < cluster_cov - 0.10, (cluster_cov, iid_cov)
    repo.close()


def test_every_replication_is_replayable_from_repository(tmp_path):
    db = tmp_path / "e.db"
    repo = RunRepository(db)
    svc, folds = _service(repo)
    run_scenario(
        "both_correct", svc,
        replications=5, n=1000, tau=2.0, base_seed=1100,
        folds=folds, run_id_prefix="replay",
    )
    rows = repo.list_runs()
    assert len(rows) == 5
    rid = rows[0]["run_id"]
    row = repo.get(rid)
    assert row["status"] == "succeeded"
    # Evidence needed to reconstruct the judgement is present.
    ev = row["evidence"]
    assert {"estimate", "se", "ci", "ci_covers_known", "z_vs_known",
            "fold_diagnostics", "config"} <= set(ev)
    assert ev["known_effect"] == 2.0
    repo.close()


def test_full_experiment_entrypoint_writes_all_four_scenarios(tmp_path):
    config = {
        "replications": 12,
        "n": 1000,
        "seed": 31337,
        "tau": 2.0,
        "folds": {"n_splits": 5, "seed": 31338, "stratified": True},
        "cluster": {
            "replications": 12,
            "n_clusters": 100,
            "cluster_size": 10,
            "icc": 0.6,
            "cluster_randomized": True,
            "seed": 31339,
        },
    }
    out = run_experiment(config, tmp_path / "full.db")
    assert set(out["iid_scenarios"]) == {
        "both_correct", "propensity_only", "outcome_only", "both_wrong"
    }
    for name, s in out["iid_scenarios"].items():
        assert s["summary"]["replications"] == 12
    assert out["cluster_experiment"]["clustered"] is True
