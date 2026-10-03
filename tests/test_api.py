"""API boundary tests: endpoints, error-category mapping, provenance access."""
import math

import pytest
from fastapi.testclient import TestClient

from seqdist.api import create_app
from seqdist.service import MAX_BOOTSTRAP_REPLICATES


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "api_test.sqlite3"))
    with TestClient(app) as c:
        yield c


def test_post_distance_ok_and_provenance_roundtrip(client):
    payload = {
        "seq1": "ACGTACGTACGTACGTACGT",
        "seq2": "ACGTACGTACGTACGAACGT",
        "model": "jc69",
        "n_replicates": 200,
        "alpha": 0.05,
        "seed": 13,
    }
    resp = client.post("/v1/distance", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # hand-computed JC69 for p = 1/20
    assert body["distance"] == pytest.approx(-0.75 * math.log(1 - 4 * 0.05 / 3))
    assert body["sites"]["n_valid"] == 20
    assert body["sites"]["n_match"] == 19
    ci = body["confidence_interval"]
    assert ci["status"] == "ok" and ci["seed"] == 13
    assert ci["lower"] <= body["distance"] <= ci["upper"]
    assert body["model_assumptions"] and body["model_valid_domain"]

    run_id = body["run_id"]
    rec = client.get(f"/v1/runs/{run_id}")
    assert rec.status_code == 200
    assert rec.json()["result"]["run_id"] == run_id
    assert rec.json()["intermediates"]["n_valid"] == 20


def test_post_distance_via_fasta(client):
    fasta = ">a\nACGTACGT\n>b\nACGTACGA\n"
    resp = client.post("/v1/distance", json={"fasta": fasta, "model": "p",
                                             "n_replicates": 50, "seed": 1})
    assert resp.status_code == 200
    assert resp.json()["distance"] == pytest.approx(1 / 8)


def test_saturated_result_is_200_with_null_distance(client):
    resp = client.post("/v1/distance", json={
        "seq1": "AAAA", "seq2": "AGCT", "model": "jc69",
        "n_replicates": 10, "seed": 0,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "saturated"
    assert body["distance"] is None
    assert "1-4p/3" in body["reason"]


def test_invalid_character_maps_to_400_input_error(client):
    resp = client.post("/v1/distance", json={"seq1": "ACGX", "seq2": "ACGT"})
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_unequal_lengths_map_to_400(client):
    resp = client.post("/v1/distance", json={"seq1": "ACGT", "seq2": "ACG"})
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_unknown_model_maps_to_400(client):
    resp = client.post("/v1/distance", json={"seq1": "ACGT", "seq2": "ACGT",
                                             "model": "tn93"})
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_all_missing_maps_to_422_computation_failure(client):
    resp = client.post("/v1/distance", json={"seq1": "NNNN", "seq2": "N-NN"})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "computation_failure"


def test_excessive_replicates_map_to_413_resource_exhausted(client):
    resp = client.post("/v1/distance", json={
        "seq1": "ACGT", "seq2": "ACGT",
        "n_replicates": MAX_BOOTSTRAP_REPLICATES + 1,
    })
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_missing_run_maps_to_404(client):
    resp = client.get("/v1/runs/doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "not_found"


def test_models_endpoint_lists_assumptions(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"p", "jc69", "k80"}
    assert any("equal" in a for a in body["jc69"]["assumptions"])
    assert any("transition" in a.lower() for a in body["k80"]["assumptions"])
