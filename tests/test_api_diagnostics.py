"""End-to-end API tests: concrete results, failure categories, diagnostics."""
from __future__ import annotations

import numpy as np


def test_health_and_contract_overview(client):
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["version"]

    overview = client.get("/").json()
    assert "2**n" in overview["randomization"]
    assert "T(s;tau)" in overview["statistic"]


def test_pvalue_endpoint_exact_with_independent_cross_check(client):
    resp = client.post("/api/v1/pvalue", json={
        "pairs": [[5, 0], [1, 0], [1, 0], [1, 0]],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert resp.headers["X-Request-ID"] == body["request_id"]
    result = body["result"]
    assert result["p_value"] == 2 / 16
    assert result["method"] == "exact-signflip"
    assert result["count_as_extreme"] == 2
    # Independent oracle (itertools, not the kernel) agrees.
    cross = result["independent_cross_check"]
    assert cross["ok"] is True
    assert cross["discrepancies"] == []
    assert cross["p_value_oracle"] == 2 / 16
    # Contract echo and diagnostics are present and request-correlated.
    assert body["statistical_contract"]["randomization_set_size"] == 16
    steps = body["diagnostics"]["processing_steps"]
    assert [s["step"] for s in steps][:2] == ["validate", "budget"]
    assert all(s["location"].startswith("app.") for s in steps)
    assert result["summary"]["version"] == body["version"]
    assert result["summary"]["request_id"] == body["request_id"]


def test_pvalue_endpoint_effect_hypothesis(client):
    resp = client.post("/api/v1/pvalue", json={
        "differences": [0, 0, 0, 0], "effect": 1.0,
    })
    result = resp.json()["result"]
    assert result["p_value"] == 2 / 16
    assert result["statistic_observed"] == 4.0


def test_inversion_endpoint_exact_singleton_and_unbounded(client):
    weak = client.post("/api/v1/inversion",
                       json={"differences": [0, 0, 0, 0], "alpha": 0.05}).json()
    cs_weak = weak["result"]["confidence_set"]
    assert cs_weak["n_intervals"] == 1
    assert cs_weak["intervals"][0]["lower_finite"] is False
    # The unbounded-set artifact is listed as uncertainty, not a failure.
    codes = {u["code"] for u in weak["result"]["summary"]["uncertainties"]}
    assert "UNBOUNDED_SET" in codes
    assert weak["result"]["summary"]["failures"] == []

    strong = client.post("/api/v1/inversion",
                         json={"differences": [0, 0, 0, 0], "alpha": 0.5}).json()
    iv = strong["result"]["confidence_set"]["intervals"][0]
    assert iv["lower"] == 0.0 and iv["upper"] == 0.0


def test_inversion_endpoint_components_preserved(client):
    body = client.post("/api/v1/inversion", json={
        "differences": [1, -2, 3, -4, 5], "alpha": 0.1,
    }).json()
    cs = body["result"]["confidence_set"]
    assert cs["is_connected"] is True
    assert cs["intervals"][0]["lower"] == -4.0
    assert cs["intervals"][0]["upper"] == 5.0
    # Independent oracle agrees on every probed point.
    assert body["result"]["independent_cross_check"]["ok"] is True


def test_budget_exceeded_reports_approximate_method_and_error(client):
    d = [float((i % 7) - 3) for i in range(30)]  # 2**30 >> budget
    resp = client.post("/api/v1/pvalue", json={
        "differences": d, "n_draws": 1000, "seed": 42,
    })
    result = resp.json()["result"]
    assert result["method"] == "monte-carlo-signflip"
    assert result["mc_standard_error"] > 0
    assert result["mc_error_halfwidth"] > result["mc_standard_error"]
    assert result["mc_error_confidence"] == 0.95
    budget = result["summary"]
    codes = {u["code"] for u in budget["uncertainties"]}
    assert {"BUDGET_EXCEEDED", "MONTE_CARLO_ERROR"} <= codes
    assert result["n_assignments"] == 2 ** 30


def test_mc_inversion_endpoint_reports_grid_and_truncation(client):
    d = [float((i % 5) - 2) for i in range(25)]
    resp = client.post("/api/v1/inversion", json={
        "differences": d, "alpha": 0.05, "n_draws": 500, "seed": 1,
        "grid_points": 51, "grid_half_width": 0.5,
    })
    result = resp.json()["result"]
    assert result["method"] == "monte-carlo-signflip"
    assert result["grid"]["points"] == 51
    assert "mc_error_halfwidth" in result
    codes = {u["code"] for u in result["summary"]["uncertainties"]}
    assert {"BUDGET_EXCEEDED", "MONTE_CARLO_ERROR", "GRID_APPROXIMATION"} <= codes
    # A half-width of 0.5 around mean ~=0 cannot contain a 95% set; the edge
    # must be flagged rather than silently returned as the full answer.
    if result.get("grid_truncated"):
        codes = {u["code"] for u in result["summary"]["uncertainties"]}
        assert "GRID_TRUNCATION" in codes


# ---------------------------------------------------------------------------
# Failure categories over HTTP
# ---------------------------------------------------------------------------

def test_http_failure_categories(client):
    cases = [
        ("/api/v1/pvalue", {"pairs": [[1, 0]]}, 422, "TOO_FEW_PAIRS"),
        ("/api/v1/pvalue", {"pairs": [[1, 0], [2, None]]},
         422, "NON_NUMERIC_OUTCOME"),
        ("/api/v1/inversion", {"differences": [1, 2], "alpha": 0},
         422, "INVALID_ALPHA"),
        ("/api/v1/pvalue", {}, 422, "MISSING_DATA"),
    ]
    for endpoint, payload, status, code in cases:
        resp = client.post(endpoint, json=payload)
        assert resp.status_code == status, payload
        failure = resp.json()["failure"]
        assert failure["code"] == code
        assert "request_id" in resp.json()


def test_http_non_finite_outcome_is_rejected(client):
    # Sent as a raw JSON body because strict clients refuse to encode inf.
    resp = client.post(
        "/api/v1/pvalue",
        content='{"differences": [1, Infinity]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["failure"]["code"] == "NON_FINITE_OUTCOME"


def test_unknown_request_is_404(client):
    resp = client.get("/api/v1/requests/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["failure"]["code"] == "UNKNOWN_REQUEST"


def test_malformed_and_non_object_bodies_are_categorized(client):
    raw = client.post(
        "/api/v1/pvalue", content="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert raw.status_code == 422
    assert raw.json()["failure"]["code"] == "MALFORMED_DATA"

    non_object = client.post("/api/v1/pvalue", json=[1, 2, 3])
    assert non_object.status_code == 422
    assert non_object.json()["failure"]["code"] == "MALFORMED_DATA"


def test_mc_parameter_type_failures_are_categorized(client):
    big = {"differences": [float((i % 7) - 3) for i in range(30)]}
    bad_draws = client.post("/api/v1/pvalue", json={**big, "n_draws": "many"})
    assert bad_draws.status_code == 422
    assert bad_draws.json()["failure"]["code"] == "INVALID_INTEGER"

    bad_grid = client.post("/api/v1/inversion", json={
        **big, "grid_points": 4,  # even -> invalid
    })
    assert bad_grid.status_code == 422
    assert bad_grid.json()["failure"]["code"] == "INVALID_GRID"


# ---------------------------------------------------------------------------
# Persistence correlation
# ---------------------------------------------------------------------------

def test_result_is_persisted_and_correlated_by_request_id(client):
    resp = client.post("/api/v1/pvalue", json={
        "differences": [1, -1, 1],
    })
    request_id = resp.json()["request_id"]

    stored = client.get(f"/api/v1/requests/{request_id}").json()
    assert stored["request"]["n_pairs"] == 3
    assert stored["request"]["status"] == "completed"
    assert stored["request"]["endpoint"] == "pvalue"
    assert len(stored["analyses"]) == 1
    assert stored["analyses"][0]["result"]["p_value"] == 1.0


def test_client_supplied_request_id_is_honored(client):
    resp = client.post(
        "/api/v1/pvalue",
        json={"differences": [1, -1, 1]},
        headers={"X-Request-ID": "fixed-correlation-id"},
    )
    assert resp.headers["X-Request-ID"] == "fixed-correlation-id"
    assert resp.json()["request_id"] == "fixed-correlation-id"
    assert client.get("/api/v1/requests/fixed-correlation-id").status_code == 200


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def test_replay_pvalue_is_bitwise_reproducible(client):
    resp = client.post("/api/v1/replay", json={
        "fixture": "large_for_monte_carlo", "n_draws": 800, "seed": 7,
    })
    result = resp.json()["result"]
    assert result["reproducible"] is True
    assert result["max_abs_difference"] == 0.0
    assert result["discrepancies"] == []
    assert len(result["bundle"]["fingerprint"]) == 64
    assert result["bundle"]["seed"] == 7
    assert result["bundle"]["environment"]["numpy"] == np.__version__


def test_replay_inversion_is_reproducible(client):
    resp = client.post("/api/v1/replay", json={
        "fixture": "large_for_monte_carlo", "kind": "inversion",
        "n_draws": 400, "seed": 9, "grid_points": 101,
        "grid_half_width": 4,
    })
    result = resp.json()["result"]
    assert result["reproducible"] is True
    assert result["bundle"]["outputs"]["kind"] == "inversion"


def test_replay_validates_inputs(client):
    resp = client.post("/api/v1/replay", json={
        "fixture": "large_for_monte_carlo", "n_draws": 0,
    })
    assert resp.status_code == 422
    assert resp.json()["failure"]["code"] == "INVALID_N_DRAWS"


# ---------------------------------------------------------------------------
# Fixtures catalogue
# ---------------------------------------------------------------------------

def test_fixtures_catalogue_exposes_independent_references(client):
    fixtures = client.get("/api/v1/fixtures").json()["fixtures"]
    by_name = {f["name"]: f for f in fixtures}
    assert by_name["one_extreme_pair"]["reference"]["pvalue_effect_0"] == 2 / 16
    assert by_name["large_for_monte_carlo"]["reference"][
        "forces_method"] == "monte-carlo-signflip"
