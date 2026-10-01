"""Integration tests: SQLite storage + service orchestration."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.errors import ErrorCode, EstimationError
from app.core import synthetic
from app.core.config import load_config
from app.storage.db import Database
from app.storage.service import ExperimentService


@pytest.fixture()
def service(config_path, tmp_path):
    cfg = load_config(str(config_path))
    db = Database(str(tmp_path / "int.db"))
    yield ExperimentService(db, cfg)
    db.close()


def _rows(ds, exclude_leak=True):
    rows = []
    names = [d.name for d in ds.declarations if not (exclude_leak and d.name == "x_post_leak")]
    for i in range(len(ds.outcome)):
        rows.append({
            "unit_id": str(ds.unit_id[i]),
            "treatment": int(ds.treatment[i]),
            "outcome": float(ds.outcome[i]),
            "covariates": {
                name: (None if np.isnan(ds.covariates[name][i]) else float(ds.covariates[name][i]))
                for name in names
            },
        })
    return rows


@pytest.mark.integration
def test_full_run_persists_completed_result_and_side_by_side_estimates(service):
    ds = synthetic.generate("balanced", n=1200, true_effect=2.0, seed=101)
    decls = [{"name": d.name, "pre_treatment": d.pre_treatment}
             for d in ds.declarations if d.name != "x_post_leak"]
    service.register("exp-1", "synthetic", decls)
    assert service.upload("exp-1", _rows(ds)) == 1200

    payload = service.run_estimation("exp-1", run_id="int-run-001")

    assert payload["status"] == "completed"
    assert payload["run_id"] == "int-run-001"
    assert payload["unadjusted"] and payload["adjusted"]
    assert payload["adjusted"]["se"] < payload["unadjusted"]["se"]
    assert payload["variance_reduction"] > 0.8
    assert abs(payload["adjusted"]["estimate"] - 2.0) < 0.15
    assert payload["theta"]["source"] == "control"
    assert payload["theta"]["n_arms_used"] == "control"
    assert set(payload["versions"]) == {"app", "numpy", "scipy"}

    # Re-read from storage.
    fetched = service.get_run("int-run-001")
    assert fetched["adjusted"]["estimate"] == payload["adjusted"]["estimate"]


@pytest.mark.integration
def test_failed_run_is_persisted_with_status_failed(service):
    ds = synthetic.generate("leakage", n=800, seed=102)
    decls = [{"name": d.name, "pre_treatment": d.pre_treatment} for d in ds.declarations]
    service.register("exp-leak", "has post-treatment field", decls)
    service.upload("exp-leak", _rows(ds, exclude_leak=False))

    with pytest.raises(EstimationError) as exc:
        service.run_estimation("exp-leak", run_id="int-run-fail")
    assert exc.value.code is ErrorCode.LEAKED_COVARIATE

    stored = service.get_run("int-run-fail")
    # The failure must not be retrievable as a success.
    assert stored["status"] == "failed"
    assert stored["error_code"] == "LEAKED_COVARIATE"
    assert stored["message"]
    assert "unadjusted" not in stored


@pytest.mark.integration
def test_undeclared_covariate_in_upload_is_rejected(service):
    ds = synthetic.generate("balanced", n=300, seed=103)
    service.register("exp-2", "synthetic", [{"name": "x_pre", "pre_treatment": True}])
    rows = _rows(ds)
    rows[0]["covariates"]["rogue_field"] = 1.0
    with pytest.raises(EstimationError) as exc:
        service.upload("exp-2", rows)
    assert exc.value.code is ErrorCode.UNDECLARED_COVARIATE


@pytest.mark.integration
def test_unknown_experiment_and_run_have_distinct_codes(service):
    with pytest.raises(EstimationError) as exc:
        service.run_estimation("nope", run_id="r")
    assert exc.value.code is ErrorCode.UNKNOWN_EXPERIMENT
    with pytest.raises(EstimationError) as exc:
        service.get_run("missing-run")
    assert exc.value.code is ErrorCode.UNKNOWN_RUN


@pytest.mark.integration
def test_missing_covariate_values_round_trip_through_storage(service):
    ds = synthetic.generate("balanced", n=900, seed=104, missing_fraction=0.08)
    decls = [{"name": d.name, "pre_treatment": d.pre_treatment}
             for d in ds.declarations if d.name != "x_post_leak"]
    service.register("exp-miss", "synthetic", decls)
    service.upload("exp-miss", _rows(ds))
    payload = service.run_estimation("exp-miss", missing_strategy="mean_impute", run_id="int-miss")
    assert payload["status"] == "completed"
    imputed = sum(d["n_imputed"] for d in payload["diagnostics"])
    assert imputed > 0
