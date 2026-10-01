"""HTTP integration tests with dependency overrides (per FastAPI testing rule).

The settings/store dependencies are overridden with in-memory instances and
the overrides are cleared afterwards.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.dependencies import configure, get_settings, get_store, reset
from app.datasets import sharp_jump, sparse_boundary
from app.store import RunStore


@pytest.fixture
def client():
    settings = Settings(
        db_path=":memory:",
        min_obs_per_side=10,
        bootstrap_reps=99,
        default_alpha=0.05,
        log_level="WARNING",
    )
    store = RunStore(":memory:")
    configure(settings, store)  # pin in-memory singletons before app builds
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as c:
        yield c, settings, store
    app.dependency_overrides.clear()
    reset()


def _points(x, y):
    return [{"x": float(a), "y": float(b)} for a, b in zip(x, y)]


@pytest.mark.integration
def test_health_and_version(client) -> None:
    c, _, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    v = c.get("/version").json()
    assert {"app", "python", "numpy", "scipy", "fastapi"} <= set(v)


@pytest.mark.integration
def test_fixtures_listed_and_sampled(client) -> None:
    c, _, _ = client
    names = {f["name"] for f in c.get("/api/v1/fixtures").json()["fixtures"]}
    assert {"sharp_jump", "no_jump", "density_sorting", "sparse_boundary"} <= names
    r = c.post("/api/v1/fixtures/sharp_jump/sample", params={"n": 500, "seed": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["true_tau"] == 3.0
    assert len(body["data"]) == 500


@pytest.mark.integration
def test_analyze_ok_persisted_and_retrievable(client) -> None:
    c, _, store = client
    dgp = sharp_jump(n=2000, seed=1)
    r = c.post(
        "/api/v1/rd/analyze",
        json={
            "input_label": "http-sharp",
            "data": _points(dgp.x, dgp.y),
            "bandwidth_method": "manual",
            "bandwidth": 0.25,
            "bootstrap_reps": 0,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["estimate"]["tau"] == pytest.approx(3.0, abs=0.2)
    run_id = body["run_id"]

    stored = c.get(f"/api/v1/runs/{run_id}")
    assert stored.status_code == 200
    assert stored.json()["response"]["estimate"]["tau"] == pytest.approx(
        body["estimate"]["tau"]
    )
    assert store.get(run_id)["status"] == "ok"


@pytest.mark.integration
def test_unidentified_is_200_with_explicit_status_not_error(client) -> None:
    c, _, _ = client
    dgp = sparse_boundary(n=600, seed=2)
    r = c.post(
        "/api/v1/rd/analyze",
        json={
            "input_label": "http-sparse",
            "data": _points(dgp.x, dgp.y),
            "bandwidth_method": "manual",
            "bandwidth": 0.1,
            "bootstrap_reps": 0,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unidentified"
    assert body["error_code"] == "singular_fit"
    assert body["estimate"] is None


@pytest.mark.integration
def test_pipeline_error_maps_to_422_with_code(client) -> None:
    c, _, _ = client
    x = np.linspace(0.1, 1, 30)  # no points below cutoff
    r = c.post(
        "/api/v1/rd/analyze",
        json={
            "input_label": "http-bad",
            "data": _points(x, np.zeros(30)),
            "bandwidth_method": "manual",
            "bandwidth": 0.5,
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error_code"] == "invalid_input"
    assert detail["error_message"]


@pytest.mark.integration
def test_schema_validation_rejects_bad_payload(client) -> None:
    c, _, _ = client
    r = c.post(
        "/api/v1/rd/analyze",
        json={"input_label": "tiny", "data": [{"x": 0.0, "y": 0.0}]},
    )
    assert r.status_code == 422  # pydantic min_length / structure


@pytest.mark.integration
def test_run_lookup_unknown_is_404(client) -> None:
    c, _, _ = client
    assert c.get("/api/v1/runs/does-not-exist").status_code == 404


@pytest.mark.integration
def test_rate_limit_returns_429(client) -> None:
    c, _, _ = client
    # tighten the limiter to a small budget for this test
    from app.api import FixedWindowRateLimiter

    c.app.state.limiter = FixedWindowRateLimiter(max_events=3, window_s=10.0)
    dgp = sharp_jump(n=200, seed=3)
    payload = {
        "input_label": "limit",
        "data": _points(dgp.x, dgp.y),
        "bandwidth_method": "manual",
        "bandwidth": 0.25,
        "bootstrap_reps": 0,
    }
    statuses = [c.post("/api/v1/rd/analyze", json=payload).status_code for _ in range(5)]
    assert statuses.count(429) == 2
