"""Run service, repository lifecycle, structured logging and error category
distinction at the application layer."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from aipw_backend.config import AipwConfig, FoldConfig, ModelConfig
from aipw_backend.dgp import generate_sample
from aipw_backend.errors import (
    ComputationError,
    InputError,
    ResourceExhaustedError,
    StateConflictError,
)
from aipw_backend.repository import RunRepository
from aipw_backend.service import RunService


def _service(db_path, **cfg_over):
    defaults = {
        "folds": FoldConfig(n_splits=5, seed=1),
        "treatment_model": ModelConfig("logistic_ridge"),
        "outcome_model": ModelConfig("ols_ridge"),
    }
    defaults.update(cfg_over)
    cfg = AipwConfig(**defaults)
    return RunService(RunRepository(db_path), cfg)


def test_successful_run_persists_evidence_and_status(tmp_path):
    svc = _service(tmp_path / "r.db")
    sample = generate_sample(2000, seed=1, tau=2.0)
    ev = svc.run(sample.x, sample.a, sample.y, known_effect=2.0)
    row = svc.repo.get(ev["run_id"])
    assert row["status"] == "succeeded"
    assert row["evidence"]["estimate"] == ev["estimate"]
    assert row["evidence"]["ci_covers_known"] is True
    assert ev["independent_unit"] == "individual"
    assert "folds" in ev and len(ev["fold_diagnostics"]) == 5


def test_run_id_reuse_is_state_conflict(tmp_path):
    svc = _service(tmp_path / "r.db")
    sample = generate_sample(2000, seed=2, tau=2.0)
    svc.run(sample.x, sample.a, sample.y, run_id="fixed-id")
    with pytest.raises(StateConflictError, match="already exists"):
        svc.run(sample.x, sample.a, sample.y, run_id="fixed-id")
    # First run's evidence is untouched.
    row = svc.repo.get("fixed-id")
    assert row["status"] == "succeeded"


def test_illegal_lifecycle_transitions_are_state_conflicts(tmp_path):
    repo = RunRepository(tmp_path / "r.db")
    repo.create("r1", {"k": 1})
    repo.mark_running("r1")
    repo.mark_succeeded("r1", {"ok": True})
    with pytest.raises(StateConflictError, match="illegal transition"):
        repo.mark_failed("r1", "computation_failure", "late")
    with pytest.raises(StateConflictError, match="unknown run_id"):
        repo.mark_running("ghost")


def test_input_failure_recorded_with_category_and_terminal_state(tmp_path):
    svc = _service(tmp_path / "r.db")
    x = np.random.default_rng(0).standard_normal((40, 2))
    y = np.random.default_rng(1).standard_normal(40)
    with pytest.raises(InputError):
        svc.run(x, np.zeros(40, dtype=int), y, run_id="bad-arm")
    row = svc.repo.get("bad-arm")
    assert row["status"] == "failed"
    assert row["error_category"] == "input_error"
    assert "both treatment arms" in row["error_message"]


def test_resource_exhaustion_is_distinct_category(tmp_path):
    svc = _service(tmp_path / "r.db", max_feature_cells=1000)
    rng = np.random.default_rng(0)
    x = rng.standard_normal((40, 100))
    a = (rng.random(40) < 0.5).astype(int)
    y = rng.standard_normal(40)
    with pytest.raises(ResourceExhaustedError):
        svc.run(x, a, y, run_id="too-big")
    row = svc.repo.get("too-big")
    assert row["error_category"] == "resource_exhausted"
    assert row["error_details"]["budget"] == 1000


def test_computation_failure_is_distinct_category(tmp_path):
    svc = _service(
        tmp_path / "r.db",
        treatment_model=ModelConfig("logistic_ridge", penalty=1e-6),
    )
    rng = np.random.default_rng(3)
    n = 60
    x = rng.standard_normal((n, 1)) * 10
    a = (x[:, 0] > 0).astype(int)  # deterministic separation
    y = rng.standard_normal(n)
    with pytest.raises(ComputationError, match="positivity|boundary"):
        svc.run(x, a, y, run_id="sep")
    row = svc.repo.get("sep")
    assert row["error_category"] == "computation_failure"


def test_logs_carry_run_id_marker_and_intermediate_state(tmp_path, caplog):
    logger = logging.getLogger("aipw.test")
    logger.setLevel(logging.INFO)
    svc = _service(tmp_path / "r.db")
    svc.log = logger
    sample = generate_sample(2000, seed=4, tau=2.0)
    with caplog.at_level(logging.INFO, logger="aipw.test"):
        ev = svc.run(sample.x, sample.a, sample.y, run_id="rid-123")
    markers = {r.message.split()[0] for r in caplog.records}
    # Newer logging call passes message JSON in the same string; check records
    # via the marker extra instead where present.
    marker_vals = {getattr(r, "marker", None) for r in caplog.records}
    assert {
        "RUN_QUEUED",
        "RUN_STARTED",
        "INPUT_ACCEPTED",
        "CROSSFIT_DONE",
        "RUN_SUCCEEDED",
    } <= marker_vals
    assert all(getattr(r, "run_id", None) == "rid-123" for r in caplog.records)
    crossfit = [r for r in caplog.records if r.marker == "CROSSFIT_DONE"][0]
    assert "pscore_min" in crossfit.message
    assert ev["run_id"] == "rid-123"


def test_cluster_run_records_cluster_as_independent_unit(tmp_path):
    svc = _service(tmp_path / "r.db")
    sample = generate_sample(
        1000, seed=5, tau=2.0, n_clusters=100, cluster_size=10, icc=0.5
    )
    ev = svc.run(sample.x, sample.a, sample.y, sample.cluster)
    assert ev["independent_unit"] == "cluster"
    assert ev["n_clusters"] == 100
