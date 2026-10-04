"""API boundary: status codes per error category, provenance, idempotency."""

from __future__ import annotations

import pytest

TOL = 1e-9


def _matrix_payload(labels, distances, **overrides):
    payload = {
        "input_format": "matrix",
        "matrix": {"labels": labels, "distances": distances},
    }
    payload.update(overrides)
    return payload


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_build_tree_ok(client, load_fixture):
    fx = load_fixture("additive_4taxon.json")
    response = client.post(
        "/v1/trees", json=_matrix_payload(fx["labels"], fx["matrix"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["newick"] == fx["expected"]["newick"]
    assert body["leaf_map"] == fx["expected"]["leaf_map"]
    assert body["residuals"]["sum_abs"] == pytest.approx(0.0, abs=TOL)
    assert body["replayed"] is False
    assert any(e["type"] == "q_tie" for e in body["events"])

    # Provenance: the run is retrievable and matches the response.
    run = client.get(f"/v1/runs/{body['run_id']}")
    assert run.status_code == 200
    record = run.json()
    assert record["newick"] == body["newick"]
    assert record["status"] == "ok"
    assert record["events"] == body["events"]


def test_fasta_input_ok(client, load_fixture):
    fx = load_fixture("duplicate_leaves.json")
    response = client.post(
        "/v1/trees", json={"input_format": "fasta", "sequences": fx["fasta"]}
    )
    assert response.status_code == 200
    assert response.json()["newick"] == fx["expected"]["newick"]


def test_asymmetric_matrix_is_422_input_validation(client):
    response = client.post(
        "/v1/trees",
        json=_matrix_payload(["a", "b"], [[0, 1], [2, 0]]),
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["category"] == "INPUT_VALIDATION"
    assert "symmetric" in error["message"]


def test_missing_matrix_for_format_is_422(client):
    response = client.post("/v1/trees", json={"input_format": "matrix"})
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "INPUT_VALIDATION"


def test_max_taxa_exceeded_is_413_resource_exhausted(client):
    payload = _matrix_payload(
        ["a", "b", "c"],
        [[0, 1, 2], [1, 0, 3], [2, 3, 0]],
        options={"max_taxa": 2},
    )
    response = client.post("/v1/trees", json=payload)
    assert response.status_code == 413
    assert response.json()["error"]["category"] == "RESOURCE_EXHAUSTED"


def test_negative_branch_error_mode_is_500_computation_failed(
    client, load_fixture
):
    fx = load_fixture("negative_branch_4taxon.json")
    payload = _matrix_payload(
        fx["labels"], fx["matrix"], options={"negative_branch_mode": "error"}
    )
    response = client.post("/v1/trees", json=payload)
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["category"] == "COMPUTATION_FAILED"
    assert error["details"]["length"] == pytest.approx(-3.5, abs=TOL)

    # The failure is itself a provenance record, retrievable by run id.
    assert error["run_id"]
    run = client.get(f"/v1/runs/{error['run_id']}")
    assert run.status_code == 200
    record = run.json()
    assert record["status"] == "error"
    assert record["error"]["category"] == "COMPUTATION_FAILED"


def test_unknown_run_is_404(client):
    response = client.get("/v1/runs/doesnotexist")
    assert response.status_code == 404
    assert response.json()["error"]["category"] == "NOT_FOUND"


def test_idempotent_replay_returns_stored_result(client, load_fixture):
    fx = load_fixture("additive_4taxon.json")
    payload = _matrix_payload(fx["labels"], fx["matrix"], request_id="req-1")
    first = client.post("/v1/trees", json=payload)
    assert first.status_code == 200
    second = client.post("/v1/trees", json=payload)
    assert second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["replayed"] is True
    assert second.json()["newick"] == first.json()["newick"]


def test_request_id_reuse_with_different_payload_is_409(client, load_fixture):
    fx = load_fixture("additive_4taxon.json")
    first = client.post(
        "/v1/trees",
        json=_matrix_payload(fx["labels"], fx["matrix"], request_id="req-x"),
    )
    assert first.status_code == 200
    conflict = client.post(
        "/v1/trees",
        json=_matrix_payload(
            ["a", "b"], [[0, 1], [1, 0]], request_id="req-x"
        ),
    )
    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["category"] == "STATE_CONFLICT"
    assert error["details"]["request_id"] == "req-x"
