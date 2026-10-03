"""End-to-end API tests: concrete result values, failure categories and
provenance — not just 'the endpoint responds'."""
import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app
from tests.reference_data import (
    M2_MATRIX,
    SCAN_CAGT_AG_BH,
    SCAN_CAGT_AG_BONFERRONI,
    SCAN_CAGT_N_TESTS,
    SCORE_AG,
)

M2_BODY = {
    "motif": {"name": "M2", "matrix": M2_MATRIX},
    "pseudocount": 1.0,
    "background": {"A": 0.25, "C": 0.25, "G": 0.25, "T": 0.25},
}


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def _scan_body(**overrides):
    body = {
        **M2_BODY,
        "sequences": [{"id": "s1", "sequence": "CAGT"}],
        "alpha": 0.1,
        "unknown_base_policy": "skip",
    }
    body.update(overrides)
    return body


def test_health_and_version(client):
    assert client.get("/health").json() == {"status": "ok"}
    v = client.get("/v1/version").json()
    assert v["app"] == __version__
    assert "numpy" in v and "python" in v


def test_scan_returns_hand_computed_hit(client):
    resp = client.post("/v1/scan", json=_scan_body())
    assert resp.status_code == 200
    body = resp.json()

    assert body["n_scored_windows"] == SCAN_CAGT_N_TESTS
    assert body["n_skipped_windows"] == 0
    assert body["threshold"]["achievable"] is True
    assert body["threshold"]["score"] == pytest.approx(SCORE_AG)

    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert (hit["seq_id"], hit["start"], hit["end"], hit["strand"]) == ("s1", 1, 3, "+")
    assert hit["matched_sequence"] == "AG"
    assert hit["score"] == pytest.approx(SCORE_AG)
    assert hit["pvalue"] == pytest.approx(0.0625)
    assert hit["pvalue_bonferroni"] == pytest.approx(SCAN_CAGT_AG_BONFERRONI)
    assert hit["pvalue_bh"] == pytest.approx(SCAN_CAGT_AG_BH)

    # raw-significant at alpha=0.1 but not BH-significant -> uncertain
    assert hit["significant_raw"] is True
    assert hit["significant_adjusted"] is False
    assert body["uncertain_hit_ids"] == [hit["hit_id"]]
    # caveats must state that statistics != biology
    assert any("not evidence of biological function" in c for c in body["caveats"])


def test_scan_unachievable_alpha_yields_no_hits(client):
    body = client.post("/v1/scan", json=_scan_body(alpha=0.05)).json()
    assert body["threshold"]["achievable"] is False
    assert body["threshold"]["score"] is None
    assert body["hits"] == []
    assert any("No score in the null distribution" in c for c in body["caveats"])


def test_scan_skip_policy_reports_skipped_windows(client):
    body = client.post(
        "/v1/scan",
        json=_scan_body(sequences=[{"id": "s1", "sequence": "ANG"}]),
    ).json()
    assert body["n_scored_windows"] == 0
    assert body["n_skipped_windows"] == 4
    assert body["hits"] == []
    assert {w["reason"] for w in body["skipped_windows"]} == {"unknown_base"}
    assert any("skipped" in c for c in body["caveats"])


def test_scan_zero_background_is_declared_failure(client):
    resp = client.post(
        "/v1/scan",
        json=_scan_body(background={"A": 0.5, "C": 0.5, "G": 0.0, "T": 0.0}),
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["category"] == "zero_background_probability"
    assert err["details"]["base"] == "G"


def test_scan_duplicate_sequence_ids_rejected(client):
    resp = client.post(
        "/v1/scan",
        json=_scan_body(
            sequences=[{"id": "s1", "sequence": "AG"}, {"id": "s1", "sequence": "CT"}]
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "duplicate_sequence_id"


def test_scan_invalid_character_rejected(client):
    resp = client.post(
        "/v1/scan", json=_scan_body(sequences=[{"id": "s1", "sequence": "ACXT"}])
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["category"] == "invalid_character"
    assert err["details"]["position"] == 2


def test_request_id_roundtrip_and_client_supplied_id(client):
    resp = client.post("/v1/scan", json=_scan_body(), headers={"x-request-id": "run-42"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "run-42"
    assert resp.json()["request_id"] == "run-42"


def test_provenance_records_completed_request(client):
    resp = client.post("/v1/scan", json=_scan_body())
    rid = resp.json()["request_id"]
    record = client.get(f"/v1/requests/{rid}").json()
    assert record["request_id"] == rid
    assert record["endpoint"] == "/v1/scan"
    assert record["status"] == "completed"
    assert record["app_version"] == __version__
    assert record["config"]["motif_length"] == 2
    assert record["input_sha256"] == resp.json()["input_sha256"]
    assert record["summary"]["n_hits"] == 1
    assert record["result"]["hits"][0]["matched_sequence"] == "AG"


def test_provenance_records_failed_request_with_category(client):
    resp = client.post(
        "/v1/scan",
        json=_scan_body(background={"A": 0.5, "C": 0.5, "G": 0.0, "T": 0.0}),
    )
    rid = resp.json()["request_id"]
    record = client.get(f"/v1/requests/{rid}").json()
    assert record["status"] == "failed"
    assert record["error"]["category"] == "zero_background_probability"


def test_provenance_unknown_id_is_404(client):
    resp = client.get("/v1/requests/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "request_not_found"


def test_calibrate_endpoint(client):
    resp = client.post("/v1/calibrate", json={**M2_BODY, "alpha": 0.1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_distinct_scores"] == 3
    assert body["motif_length"] == 2
    assert body["threshold"]["achievable"] is True
    assert body["threshold"]["score"] == pytest.approx(SCORE_AG)
    assert body["threshold"]["achieved_alpha"] == pytest.approx(0.0625)
    assert body["distribution"] is None


def test_calibrate_with_distribution(client):
    body = client.post(
        "/v1/calibrate", json={**M2_BODY, "alpha": 0.1, "include_distribution": True}
    ).json()
    dist = body["distribution"]
    assert len(dist) == 3
    tails = {round(e["score"], 6): e["tail_probability"] for e in dist}
    assert tails[round(SCORE_AG, 6)] == pytest.approx(0.0625)


def test_validate_collects_all_failures(client):
    resp = client.post(
        "/v1/validate",
        json={
            "motif": {"name": "bad", "matrix": [[1.0, 2.0]]},
            "background": {"A": 0.5, "C": 0.5, "G": 0.0, "T": 0.0},
            "alpha": 2.0,
            "unknown_base_policy": "guess",
        },
    )
    body = resp.json()
    assert body["valid"] is False
    categories = {e["category"] for e in body["errors"]}
    assert "zero_background_probability" in categories
    assert "invalid_alpha" in categories
    assert "unknown_base_policy" in categories


def test_validate_ok(client):
    body = client.post("/v1/validate", json=M2_BODY).json()
    assert body["valid"] is True
    assert body["errors"] == []
