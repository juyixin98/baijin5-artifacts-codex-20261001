"""HTTP API tests via FastAPI TestClient: concrete results + failure categories."""

from __future__ import annotations

import pytest

# Set an isolated DB for the process-wide app BEFORE importing it.
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="digest-api-")
os.environ.setdefault("DIGEST_DB_PATH", os.path.join(_tmp, "api.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_and_meta(client):
    assert client.get("/health").json()["status"] == "ok"
    meta = client.get("/api/v1/meta").json()
    assert meta["versions"]["app_version"]
    assert meta["mass_table"]["water_mass"] > 0


def test_enzyme_catalog_lists_trypsin_rule(client):
    body = client.get("/api/v1/enzymes").json()
    assert body["success"] is True
    trypsin = next(e for e in body["enzymes"] if e["name"] == "trypsin_syn")
    assert trypsin["cut_after"] == "KR"
    assert trypsin["not_before"] == "P"


def test_digest_endpoint_returns_concrete_hand_computed_result(client):
    resp = client.post(
        "/api/v1/digest",
        json={"sequence": "AAKRPA", "enzyme": "trypsin_syn",
              "missed_cleavages": 0, "run_id": "api-run-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    result = body["result"]
    assert result["cut_bonds"] == [3]
    assert result["blocked_bonds"] == [4]
    assert [f["sequence"] for f in result["fragments"]] == ["AAK", "RPA"]
    assert result["fragments"][0]["start"] == 1
    assert result["fragments"][1]["end"] == 6
    assert result["fragments"][0]["mass"]["status"] == "EXACT"


def test_digest_with_missed_cleavages(client):
    resp = client.post(
        "/api/v1/digest",
        json={"sequence": "AAKFAKLA", "enzyme": "trypsin_no_proline_rule",
              "missed_cleavages": 1, "run_id": "api-run-mc"},
    )
    frags = resp.json()["result"]["fragments"]
    # primary: AAK FAK LA ; with 1 missed also AAKFAK and FAKLA
    assert [(f["sequence"], f["missed_cleavages"]) for f in frags] == [
        ("AAK", 0), ("AAKFAK", 1), ("FAK", 0), ("FAKLA", 1), ("LA", 0),
    ]


def test_digest_unknown_residue_mass_is_flagged_not_collapsed(client):
    resp = client.post(
        "/api/v1/digest",
        json={"sequence": "AAKXAA", "enzyme": "trypsin_syn",
              "missed_cleavages": 0, "run_id": "api-run-x"},
    )
    # X is a legal token; request succeeds but the downstream fragment mass is
    # explicitly UNKNOWN with null values.
    frags = resp.json()["result"]["fragments"]
    x_frag = next(f for f in frags if "X" in f["sequence"])
    assert x_frag["mass"]["status"] == "UNKNOWN"
    assert x_frag["mass"]["neutral_mass"] is None
    # Mass positions are 1-based *within the fragment*; fragment start gives
    # the protein-relative location (4 here).
    assert x_frag["mass"]["unknown_positions"] == [1]
    assert x_frag["start"] == 4


def test_digest_ambiguous_residue_is_bounded(client):
    resp = client.post(
        "/api/v1/digest",
        json={"sequence": "AKBAA", "enzyme": "trypsin_no_proline_rule",
              "missed_cleavages": 0, "run_id": "api-run-b"},
    )
    frags = resp.json()["result"]["fragments"]
    tail = frags[-1]
    assert tail["sequence"] == "BAA"
    assert tail["mass"]["status"] == "AMBIGUOUS"
    assert tail["mass"]["min_neutral_mass"] < tail["mass"]["max_neutral_mass"]


def test_api_error_categories_and_status_codes(client):
    cases = [
        ({"sequence": "", "enzyme": "trypsin_syn"}, 422, "EMPTY_SEQUENCE"),
        ({"sequence": "AA K", "enzyme": "trypsin_syn"}, 422, "ILLEGAL_SYMBOL"),
        ({"sequence": "AAK", "enzyme": "nopease"}, 404, "ENZYME_NOT_FOUND"),
        ({"sequence": "AAK", "enzyme": "trypsin_syn", "missed_cleavages": -2},
         422, "INVALID_MISSED_CLEAVAGE"),
    ]
    for payload, status, code in cases:
        resp = client.post("/api/v1/digest", json=payload)
        assert resp.status_code == status, payload
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == code


def test_run_provenance_endpoint_round_trip(client):
    record = client.get("/api/v1/runs/api-run-1").json()["run"]
    assert record["status"] == "SUCCESS"
    assert record["input_sequence"] == "AAKRPA"
    assert [f["sequence"] for f in record["fragments"]] == ["AAK", "RPA"]


def test_run_not_found_is_categorized(client):
    resp = client.get("/api/v1/runs/missing")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_malformed_json_body_is_422_not_500(client):
    resp = client.post(
        "/api/v1/digest",
        content="{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["success"] is False
