"""Integration tests: HTTP API surface, error categories, provenance."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import settings_dependency, store_dependency
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def client(settings, store) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[settings_dependency] = lambda: settings
    app.dependency_overrides[store_dependency] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_reports_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service_version"]


def test_enzymes_lists_rules_and_blocks(client: TestClient) -> None:
    response = client.get("/enzymes")
    assert response.status_code == 200
    enzymes = {e["key"]: e for e in response.json()["enzymes"]}
    assert enzymes["trypsin"]["cleave_after"] == "KR"
    assert enzymes["trypsin"]["cterm_block"] == "P"
    assert enzymes["asp_n"]["cleave_before"] == "D"


def test_digest_returns_concrete_fragments_and_provenance(client: TestClient) -> None:
    response = client.post(
        "/digest",
        json={"sequence": "AAKAAAKAA", "enzyme": "trypsin", "missed_cleavages": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["run_id"].startswith("run-")
    assert body["fragment_count"] == 5
    first = body["fragments"][0]
    assert first["sequence"] == "AAK"
    assert first["position"] == "1-3"
    assert first["n_terminal"] is True
    # identical-string provenance: span AAKAAAK starts at 0, ends 7
    merged = body["fragments"][1]
    assert (merged["start"], merged["end"]) == (0, 7)
    assert merged["missed_cleavages"] == 1
    assert merged["mass"]["status"] == "DETERMINATE"


def test_digest_reports_charge_mz(client: TestClient) -> None:
    response = client.post(
        "/digest",
        json={"sequence": "AK", "enzyme": "trypsin", "charges": [1, 2]},
    )
    assert response.status_code == 200
    mass = response.json()["fragments"][0]["mass"]
    assert set(mass["mz_by_charge"].keys()) == {"1", "2"}
    assert mass["mz_by_charge"]["1"] > mass["nominal"]


def test_digest_unsupported_residue_is_422_with_category(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "AK!A", "enzyme": "trypsin"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "UNSUPPORTED_RESIDUE"
    assert body["position"] == 3
    assert body["residue"] == "!"
    # Error must not masquerade as a successful digest.
    assert "run_id" not in body or body.get("run_id") is None


def test_digest_empty_sequence_is_422(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "   ", "enzyme": "trypsin"})
    assert response.status_code == 422
    assert response.json()["error"] == "EMPTY_SEQUENCE"


def test_digest_invalid_enzyme_is_422(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "AK", "enzyme": "nope"})
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_ENZYME"


def test_digest_invalid_charge_is_422(client: TestClient) -> None:
    response = client.post(
        "/digest", json={"sequence": "AK", "enzyme": "trypsin", "charges": [0]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_CHARGE"


def test_digest_custom_rule_with_blocking_context(client: TestClient) -> None:
    response = client.post(
        "/digest",
        json={
            "sequence": "APAA",
            "enzyme": "custom",
            "custom_rule": {
                "cleave_after": "A",
                "cterm_block": "P",
                "name": "after-A",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [s["bond"] for s in body["blocked_sites"]] == [1]
    assert [s["bond"] for s in body["cleavage_sites"]] == [3]


def test_digest_ambiguous_flags_uncertainty(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "ABG", "enzyme": "cnbr"})
    assert response.status_code == 200
    body = response.json()
    assert body["has_ambiguous"] is True
    assert body["mass_uncertain"] is True
    mass = body["fragments"][0]["mass"]
    assert mass["status"] == "UNCERTAIN"
    assert mass["min_mass"] < mass["max_mass"]


def test_digest_j_isobaric_is_determinate(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "AJG", "enzyme": "cnbr"})
    body = response.json()
    assert body["has_ambiguous"] is True
    assert body["mass_uncertain"] is False
    assert body["fragments"][0]["mass"]["status"] == "DETERMINATE"


def test_digest_schema_validation_error_is_structured(client: TestClient) -> None:
    response = client.post("/digest", json={"enzyme": "trypsin"})  # missing sequence
    assert response.status_code == 422
    assert response.json()["error"] == "REQUEST_VALIDATION_ERROR"


def test_run_can_be_retrieved_after_digest(client: TestClient) -> None:
    created = client.post(
        "/digest", json={"sequence": "AK", "enzyme": "trypsin"}
    ).json()
    run_id = created["run_id"]
    fetched = client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["run"]["run_id"] == run_id


def test_missing_run_is_404_category(client: TestClient) -> None:
    response = client.get("/runs/run-missing")
    assert response.status_code == 404
    assert response.json()["error"] == "RUN_NOT_FOUND"


def test_validate_endpoint_pass_and_fail(client: TestClient) -> None:
    payload = {
        "expectations": [
            {
                "case_name": "ok",
                "sequence": "AAKAAAKAA",
                "enzyme": "trypsin",
                "missed_cleavages": 0,
                "fragment_count": 3,
                "cleavage_bonds": [3, 7],
            },
            {
                "case_name": "bad",
                "sequence": "AAKAAAKAA",
                "enzyme": "trypsin",
                "missed_cleavages": 0,
                "fragment_count": 99,
            },
        ]
    }
    response = client.post("/validate", json=payload)
    assert response.status_code == 200
    body = response.json()
    # Aggregate is FAIL even though one case passed - no success coercion.
    assert body["status"] == "FAIL"
    assert body["passed"] == 1 and body["failed"] == 1
    bad_case = next(c for c in body["cases"] if c["case_name"] == "bad")
    assert bad_case["verdict"] == "FAIL"
    assert bad_case["mismatches"]


def test_validate_unknown_run_id_is_404(client: TestClient) -> None:
    response = client.post(
        "/validate",
        json={"run_id": "run-nope", "expectations": []},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "RUN_NOT_FOUND"


def test_empty_n_segment_is_exposed_over_http(client: TestClient) -> None:
    response = client.post("/digest", json={"sequence": "DAAD", "enzyme": "asp_n"})
    body = response.json()
    first = body["fragments"][0]
    assert first["empty"] is True
    assert first["sequence"] == ""
    assert first["n_terminal"] is True
