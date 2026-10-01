"""End-to-end HTTP tests against the FastAPI app."""

from __future__ import annotations

import numpy as np
import pytest

from app.core import synthetic


def _payload(ds, exclude_leak=True):
    names = [d.name for d in ds.declarations if not (exclude_leak and d.name == "x_post_leak")]
    return {
        "observations": [
            {
                "unit_id": str(ds.unit_id[i]),
                "treatment": int(ds.treatment[i]),
                "outcome": float(ds.outcome[i]),
                "covariates": {
                    name: (None if np.isnan(ds.covariates[name][i]) else float(ds.covariates[name][i]))
                    for name in names
                },
            }
            for i in range(len(ds.outcome))
        ]
    }


def _register(client, exp_id, ds, exclude_leak=True):
    return client.post("/experiments", json={
        "experiment_id": exp_id,
        "description": "synthetic e2e",
        "covariates": [
            {"name": d.name, "pre_treatment": d.pre_treatment}
            for d in ds.declarations
            if not (exclude_leak and d.name == "x_post_leak")
        ],
    })


@pytest.mark.integration
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.integration
def test_register_upload_run_happy_path(client, log_file):
    ds = synthetic.generate("balanced", n=1000, true_effect=2.0, seed=201)
    assert _register(client, "e2e-1", ds).status_code == 201
    assert client.post("/experiments/e2e-1/observations", json=_payload(ds)).status_code == 201

    r = client.post("/experiments/e2e-1/runs", json={"theta_source": "control"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["unadjusted"]["se"] > body["adjusted"]["se"]
    assert abs(body["adjusted"]["estimate"] - 2.0) < 0.15
    assert body["variance_reduction"] > 0.8

    run_id = body["run_id"]
    fetched = client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id

    # Logs are tied to the run identity.
    log_text = log_file.read_text(encoding="utf-8")
    assert run_id in log_text
    assert "unadjusted | est=" in log_text
    assert "adjusted | est=" in log_text


@pytest.mark.integration
def test_leaked_covariate_returns_typed_error_and_stores_failure(client):
    ds = synthetic.generate("leakage", n=600, seed=202)
    assert _register(client, "e2e-leak", ds, exclude_leak=False).status_code == 201
    assert client.post(
        "/experiments/e2e-leak/observations", json=_payload(ds, exclude_leak=False)
    ).status_code == 201

    r = client.post("/experiments/e2e-leak/runs", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "LEAKED_COVARIATE"
    assert "x_post_leak" in body["details"]["covariates"]
    assert body["run_id"]

    stored = client.get(f"/runs/{body['run_id']}").json()
    assert stored["status"] == "failed"
    assert stored["error_code"] == "LEAKED_COVARIATE"


@pytest.mark.integration
def test_zero_variance_error_strategy_surfaces_category(client):
    ds = synthetic.generate("balanced", n=600, seed=203)
    ds.covariates["x_irrelevant"] = np.full(len(ds.outcome), 9.0)
    assert _register(client, "e2e-zv", ds).status_code == 201
    r = client.post(
        "/experiments/e2e-zv/observations",
        json=_payload(ds),
    )
    assert r.status_code == 201
    r = client.post("/experiments/e2e-zv/runs", json={"zero_variance_strategy": "error"})
    assert r.status_code == 422
    assert r.json()["error_code"] == "ZERO_VARIANCE_COVARIATE"


@pytest.mark.integration
def test_missing_strategy_error_fails_run(client):
    ds = synthetic.generate("balanced", n=600, seed=204, missing_fraction=0.1)
    assert _register(client, "e2e-miss", ds).status_code == 201
    assert client.post("/experiments/e2e-miss/observations", json=_payload(ds)).status_code == 201
    r = client.post("/experiments/e2e-miss/runs", json={"missing_strategy": "error"})
    assert r.status_code == 422
    assert r.json()["error_code"] == "COVARIATE_VALUE_MISSING"


@pytest.mark.integration
def test_runs_listing_and_unknown_experiment_404(client):
    ds = synthetic.generate("balanced", n=400, seed=205)
    _register(client, "e2e-list", ds)
    client.post("/experiments/e2e-list/observations", json=_payload(ds))
    client.post("/experiments/e2e-list/runs", json={})
    client.post("/experiments/e2e-list/runs", json={"theta_source": "pooled_fwl"})
    r = client.get("/experiments/e2e-list/runs")
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 2
    assert {run["theta"]["source"] for run in r.json()["runs"]} == {"control", "pooled_fwl"}

    assert client.get("/experiments/ghost/runs").status_code == 404
    assert client.get("/runs/does-not-exist").status_code == 404


@pytest.mark.integration
def test_invalid_request_body_does_not_return_success(client):
    r = client.post("/experiments/e2e-x/runs", json={"theta_source": "not_a_source"})
    assert r.status_code == 422
    # Pydantic schema rejection; no run row can have been fabricated.
    assert client.get("/experiments/e2e-x/runs").status_code == 404


@pytest.mark.integration
def test_create_app_resolves_default_db_path_from_config(config_path, tmp_path):
    from app.api.app import create_app

    app = create_app(config_path=str(config_path))
    try:
        assert str(app.state.service.db.path) == str(tmp_path / "test.db")
    finally:
        app.state.service.db.close()
