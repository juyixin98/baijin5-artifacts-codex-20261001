"""End-to-end API tests via FastAPI's in-process test client.

These assert concrete results and concrete failure categories -- not merely
that endpoints respond.  They also check the request id binds the response,
logs and persisted SQLite row together.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_pvalue_exact_concrete_result_and_request_binding(client):
    body = {"treated": [4, 5, 6], "control": [3, 3, 3], "method": "two_sided_abs", "tau": 0}
    # differences [1,2,3] -> exact p = 2/8
    r = client.post("/api/pvalue", json=body)
    assert r.status_code == 200
    env = r.json()
    assert env["status"] == "ok"
    rid = env["request_id"]
    assert rid.startswith("req_")
    result = env["result"]
    assert result["kind"] == "exact"
    assert result["p_value"] == pytest.approx(0.25)
    assert result["n_extreme"] == 2
    assert [s["name"] for s in env["steps"]] == ["build_design", "select_strategy", "compute_p_value"]
    for step in env["steps"]:
        assert step["location"].startswith("app.service:")

    # the same request id resolves the persisted row
    fetched = client.get(f"/api/requests/{rid}").json()["request"]
    assert fetched["request_id"] == rid
    assert fetched["computation"] == "exact"
    # inversion results are certified; a plain p-value row leaves it NULL
    assert fetched["certified"] is None
    assert fetched["payload_json"]["treated"] == [4, 5, 6]
    assert fetched["service_version"]


def test_invert_disconnected_set_is_returned_as_components(client):
    body = {
        "treated": [0, -3, 3, -3, 0],
        "control": [1, 1, 1, 1, 1],  # differences [-1,-4,2,-4,-1]
        "method": "two_sided_prob",
        "alpha": 0.1,
    }
    env = client.post("/api/invert", json=body).json()
    assert env["status"] == "ok"
    result = env["result"]
    assert result["certified"] is True
    assert result["is_disconnected"] is True
    assert result["n_components"] == 3
    rendered = [c["rendered"] for c in result["components"]]
    assert rendered[0].endswith(")") and rendered[0].startswith("(")  # open
    assert result["hull"]["rendered"] != rendered
    assert "DISCONNECTED" in env["steps"][-1]["summary"]


def test_randomization_set_is_pairwise_and_complete(client):
    body = {"treated": [10, 20], "control": [1, 2], "preview_limit": 4}
    result = client.post("/api/randomization-set", json=body).json()["result"]
    assert result["randomization_set_size"] == 4
    assert len(result["assignments"]) == 4
    flipped = {tuple(a["flipped_pairs"]) for a in result["assignments"]}
    assert flipped == {(), (0,), (1,), (0, 1)}
    for a in result["assignments"]:
        for i in range(2):
            assert set([a["treated"][i], a["control"][i]]) == {[10, 20][i], [1, 2][i]}


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------
def test_unequal_lengths_report_invalid_pairs_not_500(client):
    env = client.post("/api/pvalue", json={"treated": [1, 2], "control": [1]}).json()
    assert env["status"] == "error"
    assert env["result"] is None
    codes = [f["code"] for f in env["failures"]]
    assert "invalid_pairs" in codes


def test_bad_alpha_and_method_are_distinct_codes(client):
    base = {"treated": [1, 2, 3], "control": [0, 0, 0]}
    env = client.post("/api/invert", json={**base, "alpha": 0}).json()
    assert [f["code"] for f in env["failures"]] == ["invalid_alpha"]
    env = client.post("/api/pvalue", json={**base, "method": "one_sided_left"}).json()
    assert [f["code"] for f in env["failures"]] == ["invalid_method"]


def test_non_finite_values_are_rejected(client):
    # Send NaN as a raw token because Python's json module refuses to encode it.
    raw = b'{"treated": [1, NaN], "control": [1, 2]}'
    env = client.post(
        "/api/pvalue", content=raw, headers={"content-type": "application/json"}
    ).json()
    assert any(f["code"] == "invalid_pairs" for f in env["failures"])


def test_malformed_body_is_http_400(client):
    r = client.post("/api/pvalue", data="not-json", headers={"content-type": "application/json"})
    assert r.status_code == 400


@pytest.mark.parametrize("endpoint", ["/api/invert", "/api/randomization-set"])
def test_malformed_body_is_http_400_on_other_endpoints(client, endpoint):
    r = client.post(
        endpoint, content="[1,2,3]", headers={"content-type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["status"] == "error"


def test_unknown_route_is_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404


def test_unknown_request_id_is_404(client):
    assert client.get("/api/requests/req_doesnotexist").status_code == 404


def test_uncertainties_are_listed_separately_from_failures(tmp_path, settings):
    # force approximation through a low budget
    settings = type(settings)(
        db_path=tmp_path / "a.db",
        exact_budget=4,
        crossing_budget=4,
        mc_draws=500,
        mc_seed=1,
        inversion_grid=200,
        inversion_refine=20,
    )
    with TestClient(create_app(settings)) as c:
        body = {"treated": [1, 2, 3, 4, 5], "control": [0] * 5, "tau": 0}
        env = c.post("/api/pvalue", json=body).json()
    assert env["status"] == "ok"
    assert env["failures"] == []
    assert env["result"]["kind"] == "approximate"
    assert env["uncertainties"], "Monte Carlo answers must record an uncertainty note"
    assert env["result"]["monte_carlo_error"] > 0
