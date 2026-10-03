"""End-to-end HTTP tests asserting concrete results and failure categories."""
from __future__ import annotations

from tests.reference.brute_force import (
    is_nested_and_legal,
    optimal_structures,
    optimum_count,
    parse_dot_bracket,
)


def test_health_reports_model_metadata(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["algorithm"] == "nussinov"
    assert body["model_scope"] == "teaching-combinatorial"
    assert body["version"]


def test_fold_concrete_hairpin_result(client):
    resp = client.post("/api/v1/fold", json={"sequence": "GAAAC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["optimum"] == 1
    assert body["sequence"] == "GAAAC"
    assert body["min_loop_length"] == 3
    assert body["pseudoknots_supported"] is False
    assert "G-C" in body["allowed_pairs"] and "G-U" in body["allowed_pairs"]

    structure = body["structure"]
    assert structure["pair_count"] == 1
    assert structure["dot_bracket"] == "(...)"
    assert structure["pairs"] == [
        {
            "position_5prime": 1,
            "position_3prime": 5,
            "base_5prime": "G",
            "base_3prime": "C",
        }
    ]
    assert structure["pair_table"] == [5, 0, 0, 0, 1]
    assert body["legal_structure"] is True
    assert body["legality_violations"] == []
    assert body["alternatives"] == []

    # Dot-bracket and pair table agree when parsed independently.
    assert parse_dot_bracket(structure["dot_bracket"]) == {(0, 4)}
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]


def test_fold_no_pairable_bases(client):
    resp = client.post("/api/v1/fold", json={"sequence": "AAAA"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["optimum"] == 0
    assert body["structure"]["dot_bracket"] == "...."
    assert body["structure"]["pairs"] == []
    assert body["structure"]["pair_table"] == [0, 0, 0, 0]


def test_fold_min_loop_boundary_pair_rejected(client):
    # Four bases, one enclosed between ends -> loop 1 < 3: no pair allowed.
    resp = client.post("/api/v1/fold", json={"sequence": "GAAC"})
    assert resp.status_code == 200
    assert resp.json()["optimum"] == 0


def test_fold_multiple_optimal_structures_are_enumerated(client):
    resp = client.post(
        "/api/v1/fold",
        json={"sequence": "GGAUCC", "enumerate_alternatives": True, "alternatives_limit": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["optimum"] == 1

    expected = optimal_structures("GGAUCC")
    returned = [body["structure"]] + body["alternatives"]
    assert len(returned) == len(expected) == 3

    produced_sets = []
    ranks = []
    for item in returned:
        pairs = parse_dot_bracket(item["dot_bracket"])
        produced_sets.append(frozenset(pairs))
        ranks.append(item["rank"])
        # Internal consistency: pair table matches dot-bracket and pairs list.
        assert pairs == {(p["position_5prime"] - 1, p["position_3prime"] - 1) for p in item["pairs"]}
        assert is_nested_and_legal("GGAUCC", pairs)
        assert item["pair_count"] == body["optimum"]
    assert set(produced_sets) == expected
    assert ranks == [1, 2, 3]
    assert returned[0]["is_primary"] is True
    assert all(item["is_primary"] is False for item in returned[1:])
    assert body["alternatives_truncated"] is False


def test_fold_alternatives_limit_truncation_flag(client):
    resp = client.post(
        "/api/v1/fold",
        json={"sequence": "GGAUCC", "enumerate_alternatives": True, "alternatives_limit": 2},
    )
    body = resp.json()
    assert len([body["structure"]] + body["alternatives"]) == 2
    assert body["alternatives_truncated"] is True
    steps = {s["name"]: s for s in body["processing_steps"]}
    assert steps["enumerate_alternatives"]["outcome"] == "warning"
    assert "truncated" in steps["enumerate_alternatives"]["detail"]


def test_fold_primary_is_stable_across_requests(client):
    responses = [
        client.post("/api/v1/fold", json={"sequence": "GGAUCC"}).json()
        for _ in range(5)
    ]
    brackets = {r["structure"]["dot_bracket"] for r in responses}
    assert brackets == {".(...)"}
    # Each request keeps its own identity.
    ids = {r["request_id"] for r in responses}
    assert len(ids) == 5


def test_client_supplied_request_id_is_honored_and_correlated(client):
    resp = client.post(
        "/api/v1/fold",
        json={"sequence": "GAAAC"},
        headers={"X-Request-ID": "integration-test-id-001"},
    )
    assert resp.headers["x-request-id"] == "integration-test-id-001"
    assert resp.json()["request_id"] == "integration-test-id-001"

    lineage = client.get("/api/v1/lineage/integration-test-id-001").json()
    assert lineage["record"]["request_id"] == "integration-test-id-001"
    assert lineage["record"]["status"] == "success"
    assert lineage["record"]["optimum"] == 1
    assert lineage["record"]["primary_dot_bracket"] == "(...)"
    step_names = [s["step_name"] for s in lineage["record"]["steps"]]
    assert step_names == [
        "parse_sequence",
        "validate_parameters",
        "fill_dp_table",
        "independent_optimum_check",
        "traceback",
        "legality_check",
        "enumerate_alternatives",
        "persist_lineage",
    ]
    assert all(s["outcome"] == "ok" or s["outcome"] == "skipped" for s in lineage["record"]["steps"])
    assert lineage["record"]["algorithm_version"]
    assert lineage["record"]["processing_location"]
    assert lineage["record"]["model_scope"] == "teaching-combinatorial"
    assert lineage["record"]["structures"][0]["pair_table"] == [5, 0, 0, 0, 1]
    assert any("does NOT predict" in w or "does not predict" in w.lower() for w in lineage["record"]["warnings"])


def test_failed_request_is_persisted_with_error_category(client):
    resp = client.post("/api/v1/fold", json={"sequence": "ACGX"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["category"] == "invalid_base"
    assert body["error"]["details"]["position"] == 3
    assert body["error"]["details"]["base"] == "X"

    lineage = client.get(f"/api/v1/lineage/{body['request_id']}").json()["record"]
    assert lineage["status"] == "failed"
    assert lineage["error_category"] == "invalid_base"
    failed_step = [s for s in lineage["steps"] if s["outcome"] == "failed"]
    assert len(failed_step) == 1
    assert failed_step[0]["step_name"] == "parse_sequence"
    assert lineage["optimum"] is None


def test_empty_sequence_failure_category(client):
    resp = client.post("/api/v1/fold", json={"sequence": "   "})
    # Pydantic min_length=1 passes for whitespace, domain then rejects.
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "empty_sequence"


def test_missing_sequence_is_schema_error(client):
    resp = client.post("/api/v1/fold", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "request_schema_error"
    assert body["request_id"] == resp.headers["x-request-id"]


def test_alternatives_limit_over_server_cap_failure_category(client):
    resp = client.post(
        "/api/v1/fold",
        json={"sequence": "GAAAC", "alternatives_limit": 10_000},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "invalid_parameter"


def test_lineage_for_unknown_id_is_404(client):
    resp = client.get("/api/v1/lineage/req_does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "result_not_found"


def test_dna_fixture_is_normalized_then_solved(client):
    resp = client.post("/api/v1/fold", json={"sequence": "GAAAT"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["sequence"] == "GAAAU"
    assert body["optimum"] == 1
    assert body["structure"]["dot_bracket"] == "(...)"


def test_exhaustive_short_fixtures_match_oracle_via_api(client):
    import itertools

    # All 4^5 = 1024 length-5 sequences end to end through HTTP + SQLite.
    # (Fixture caps alternatives_limit at 100; length-5 never reaches that.)
    for chars in itertools.product("ACGU", repeat=5):
        sequence = "".join(chars)
        body = client.post(
            "/api/v1/fold",
            json={"sequence": sequence, "enumerate_alternatives": True, "alternatives_limit": 100},
        ).json()
        assert body["optimum"] == optimum_count(sequence), sequence
        returned = [body["structure"]] + body["alternatives"]
        produced = {
            frozenset(parse_dot_bracket(item["dot_bracket"])) for item in returned
        }
        assert produced == optimal_structures(sequence), sequence


def test_response_uncertainties_explicitly_disclaim_reliability(client):
    body = client.post("/api/v1/fold", json={"sequence": "GAAAC"}).json()
    joined = " ".join(body["uncertainties"]).lower()
    assert "pseudoknot" in joined
    assert "teaching" in joined
    assert "reliability" in joined


def test_logs_carry_correlated_request_id(client):
    import logging

    from nussinov_backend.logging_setup import LOGGER_NAMESPACE

    captured: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Collect()
    api_logger = logging.getLogger(f"{LOGGER_NAMESPACE}.api")
    api_logger.addHandler(handler)
    try:
        client.post(
            "/api/v1/fold",
            json={"sequence": "GAAAC"},
            headers={"X-Request-ID": "log-correlation-1"},
        )
    finally:
        api_logger.removeHandler(handler)

    assert captured  # something was logged during the request
    success_logs = [r for r in captured if "fold succeeded" in r.getMessage()]
    assert success_logs
    assert all(r.request_id == "log-correlation-1" for r in captured)
