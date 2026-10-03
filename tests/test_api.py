"""HTTP API: success shape, provenance, and failure categories end-to-end."""

import pytest

from tests.reference_motif import P_BEST, REF_COUNTS, SCORE_BEST

BASE_PAYLOAD = {
    "sequences": ">s1\nGTACC\n",
    "motif_counts": REF_COUNTS,
    "pvalue_threshold": 0.1,
}


def test_health_and_config(client):
    health = client.get("/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    config = client.get("/v1/config")
    assert config.status_code == 200
    body = config.json()
    assert body["background"] == {"A": 0.25, "C": 0.25, "G": 0.25, "T": 0.25}
    assert body["pseudocount"] == 1.0
    assert body["algorithm_version"]


def test_scan_success_shape_and_hand_computed_hits(client):
    resp = client.post("/v1/scan", json=BASE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()

    # Provenance / explainability fields.
    assert body["status"] == "ok"
    assert body["request_id"]
    assert body["request_hash"]
    assert body["app_version"] and body["algorithm_version"]
    assert body["config"]["background"]["A"] == 0.25

    # Threshold resolved against the declared background: p<=0.1 selects the
    # best word's score (tail probability 1/16).
    assert body["threshold"]["mode"] == "pvalue"
    assert body["threshold"]["score_threshold"] == pytest.approx(SCORE_BEST)
    assert body["threshold"]["achieved_pvalue"] == pytest.approx(P_BEST)

    # Hand-computed hits on "GTACC": '+' at [2,4), '-' at [0,2).
    hits = {(h["strand"], h["start"], h["end"]) for h in body["hits"]}
    assert hits == {("+", 2, 4), ("-", 0, 2)}
    assert body["summary"]["evaluated_windows"] == 8
    assert body["summary"]["enumerated_words"] == 16
    assert body["summary"]["n_hits"] == 2


def test_scan_is_persisted_and_retrievable(client):
    resp = client.post("/v1/scan", json=BASE_PAYLOAD)
    request_id = resp.json()["request_id"]

    stored = client.get(f"/v1/scan/{request_id}")
    assert stored.status_code == 200
    record = stored.json()
    assert record["status"] == "ok"
    assert record["request_hash"] == resp.json()["request_hash"]
    assert {(h["strand"], h["start"]) for h in record["hits"]} == {("+", 2), ("-", 0)}
    assert record["params"]["sequences"] == BASE_PAYLOAD["sequences"]


def test_default_pvalue_threshold_may_be_unreachable(client):
    payload = {"sequences": "GTACC", "motif_counts": REF_COUNTS}
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    # Default p=0.05 < best achievable 1/16: declared as a warning, zero hits.
    assert body["threshold"]["mode"] == "default_pvalue"
    assert body["threshold"]["score_threshold"] is None
    assert body["hits"] == []
    assert any(w["category"] == "THRESHOLD_UNREACHABLE" for w in body["summary"]["warnings"])


def test_score_threshold_mode(client):
    payload = {
        "sequences": "GTACC",
        "motif_counts": REF_COUNTS,
        "score_threshold": SCORE_BEST,
    }
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold"]["mode"] == "score"
    assert body["threshold"]["achieved_pvalue"] == pytest.approx(P_BEST)
    assert body["summary"]["n_hits"] == 2


def test_conflicting_thresholds_rejected(client):
    payload = {**BASE_PAYLOAD, "score_threshold": 0.0}
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "INVALID_THRESHOLD"


def test_invalid_sequence_rejected_with_category(client):
    payload = {**BASE_PAYLOAD, "sequences": "ACG1AC"}
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "INVALID_SEQUENCE"
    assert body["request_id"]


def test_zero_background_rejected_and_failure_persisted(client):
    payload = {
        **BASE_PAYLOAD,
        "background": {"A": 0.0, "C": 1.0 / 3, "G": 1.0 / 3, "T": 1.0 / 3},
    }
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "ZERO_BACKGROUND_PROBABILITY"

    # The failed request is itself traceable through the provenance store.
    stored = client.get(f"/v1/scan/{body['request_id']}")
    assert stored.status_code == 200
    record = stored.json()
    assert record["status"] == "failed"
    assert record["summary"]["error"]["category"] == "ZERO_BACKGROUND_PROBABILITY"


def test_motif_too_long_rejected(client):
    payload = {**BASE_PAYLOAD, "motif_counts": [[1.0, 1.0, 1.0, 1.0]] * 11}
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "MOTIF_TOO_LONG"


def test_schema_validation_error_uses_error_envelope(client):
    resp = client.post("/v1/scan", json={"sequences": "ACGT"})  # motif_counts missing
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "INVALID_REQUEST"
    assert body["request_id"]


def test_unknown_scan_id_is_404(client):
    resp = client.get("/v1/scan/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "SCAN_NOT_FOUND"


def test_unknown_base_policy_via_api(client):
    payload = {**BASE_PAYLOAD, "sequences": "ANC", "unknown_policy": "skip"}
    resp = client.post("/v1/scan", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["skipped_windows"] == 4
    assert body["summary"]["evaluated_windows"] == 0
