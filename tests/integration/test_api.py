"""End-to-end API tests: HTTP status, typed error categories, SQLite roundtrip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.storage import RunStore
from app.core.contracts import LeakagePolicy

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "test.db"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "database": {"path": str(db)},
        "logging": {"dir": str(tmp_path / "logs"), "level": "WARNING"},
    }))
    app = create_app(store=RunStore(db), config_path=cfg)
    # raise_server_exceptions=False so the generic 500 handler's response is
    # observable instead of being re-raised in the test process.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def balanced_payload():
    return json.loads((ROOT / "data" / "sample" / "balanced.json").read_text())


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


@pytest.mark.integration
def test_full_run_lifecycle(client, balanced_payload):
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Identity and side-by-side reporting.
    assert len(body["run_id"]) == 12
    assert body["unadjusted"]["estimator"] == "groups_unadjusted"
    assert body["cuped"]["estimator"] == "cuped"
    assert body["lin"]["estimator"] == "lin_ancova"
    # Known truth tau=2 recovered with large variance reduction.
    assert abs(body["cuped"]["estimate"] - 2.0) < 0.05
    assert body["cuped"]["se"] < body["unadjusted"]["se"] / 3
    # Leakage + constant covariates excluded with evidence.
    assert "post_spend" in body["dropped_covariates"]
    assert any("post_spend" in w for w in body["warnings"])

    run_id = body["run_id"]

    # Persisted and retrievable with identical content.
    fetched = client.get(f"/api/v1/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    listed = client.get("/api/v1/runs")
    assert listed.status_code == 200
    ids = [r["run_id"] for r in listed.json()["runs"]]
    assert run_id in ids


@pytest.mark.integration
def test_leakage_fail_returns_422_with_category(client, balanced_payload):
    balanced_payload["leakage_policy"] = LeakagePolicy.FAIL.value
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "LEAKAGE_DETECTED"
    assert "post_spend" in err["details"]["covariates"]


@pytest.mark.integration
def test_missing_values_default_returns_typed_400(client):
    root = ROOT
    payload = json.loads((root / "data" / "sample" / "missing.json").read_text())
    resp = client.post("/api/v1/runs", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MISSING_VALUES_PRESENT"


@pytest.mark.integration
def test_missing_values_complete_cases_succeeds(client):
    root = ROOT
    payload = json.loads((root / "data" / "sample" / "missing.json").read_text())
    payload["missing_policy"] = "complete_cases"
    resp = client.post("/api/v1/runs", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["n_complete_rows"] == 950


@pytest.mark.integration
def test_unknown_covariate_is_typed_400(client, balanced_payload):
    balanced_payload["covariates"].append("does_not_exist")
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNKNOWN_COVARIATE"


@pytest.mark.integration
def test_non_binary_treatment_is_400(client, balanced_payload):
    balanced_payload["data"]["treatment"][0] = 7
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "NON_BINARY_TREATMENT"


@pytest.mark.integration
def test_bad_enum_value_rejected_by_schema(client, balanced_payload):
    balanced_payload["se_type"] = "not_a_real_se_type"
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 422  # Pydantic validation, not a fake success


@pytest.mark.integration
def test_get_unknown_run_is_404(client):
    resp = client.get("/api/v1/runs/nonexistentid")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.integration
def test_internal_error_is_not_reported_as_success(client, monkeypatch):
    import app.api.main as main_mod

    def boom(*a, **k):
        raise RuntimeError("synthetic unexpected failure")

    monkeypatch.setattr(main_mod, "run_analysis", boom)
    root = ROOT
    payload = json.loads((root / "data" / "sample" / "balanced.json").read_text())
    resp = client.post("/api/v1/runs", json=payload)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.integration
def test_request_overrides_are_honoured(client, balanced_payload):
    balanced_payload["theta_source"] = "pooled_pre"
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 201
    assert resp.json()["cuped"]["theta_source"] == "pooled_pre"


@pytest.mark.integration
def test_given_theta_roundtrip(client, balanced_payload):
    balanced_payload["theta_source"] = "given"
    balanced_payload["given_theta"] = [3.0, 0.0]  # aligned with pre_x, unrelated
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cuped"]["theta_source"] == "given"
    assert body["cuped"]["theta_se"] is None
    assert body["cuped"]["estimate"] == pytest.approx(2.0, abs=0.05)


@pytest.mark.integration
def test_given_theta_wrong_length_is_400(client, balanced_payload):
    balanced_payload["theta_source"] = "given"
    balanced_payload["given_theta"] = [3.0]  # 2 covariates retained
    resp = client.post("/api/v1/runs", json=balanced_payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "CONFIG_ERROR"
