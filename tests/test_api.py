"""API boundary: endpoints, HTTP status mapping, error body contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from njtree.api import create_app

from .conftest import FIXTURES_DIR, load_fixture


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "api.sqlite"))
    return TestClient(app)


def _matrix_body(fx, **params):
    return {"matrix": {"labels": fx["labels"], "values": fx["matrix"]}, "params": params}


def test_build_tree_from_matrix(client):
    fx = load_fixture("additive4.json")
    resp = client.post("/v1/trees", json=_matrix_body(fx))
    assert resp.status_code == 200
    body = resp.json()
    assert body["newick"] == fx["expected"]["newick"]
    assert body["leaf_map"] == fx["expected"]["leaf_map"]
    assert body["residuals"]["total_absolute"] == pytest.approx(0.0)
    assert body["run_id"]
    assert body["idempotent"] is False


def test_build_tree_from_fasta(client):
    fx = load_fixture("additive4.json")
    fasta = (FIXTURES_DIR / "sequences.fasta").read_text()
    resp = client.post("/v1/trees", json={"fasta": fasta})
    assert resp.status_code == 200
    assert resp.json()["newick"] == fx["expected"]["newick"]


def test_exactly_one_input_required(client):
    fx = load_fixture("additive4.json")
    both = _matrix_body(fx) | {"fasta": ">A\nAAA\n>B\nAAA\n>C\nAAC\n"}
    assert client.post("/v1/trees", json=both).status_code == 422
    assert client.post("/v1/trees", json={}).status_code == 422


def test_invalid_matrix_maps_to_422_input_error(client):
    resp = client.post("/v1/trees", json={
        "matrix": {"labels": ["A", "B", "C"],
                   "values": [[0, -1, 2], [-1, 0, 3], [2, 3, 0]]}})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["category"] == "input_error"
    assert error["details"]["check"] == "non_negative"


def test_run_id_conflict_maps_to_409(client):
    fx = load_fixture("additive4.json")
    other = load_fixture("negative_branch.json")
    assert client.post("/v1/trees", json=_matrix_body(fx, run_id="x")).status_code == 200
    resp = client.post("/v1/trees", json=_matrix_body(other, run_id="x"))
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "state_conflict"


def test_idempotent_resubmission_via_api(client):
    fx = load_fixture("additive4.json")
    first = client.post("/v1/trees", json=_matrix_body(fx, run_id="idem")).json()
    second = client.post("/v1/trees", json=_matrix_body(fx, run_id="idem")).json()
    assert second["idempotent"] is True
    assert second["newick"] == first["newick"]


def test_too_many_taxa_maps_to_413(client):
    fx = load_fixture("additive4.json")
    resp = client.post("/v1/trees", json=_matrix_body(fx, max_taxa=3))
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_negative_branch_error_mode_maps_to_500(client):
    fx = load_fixture("negative_branch.json")
    resp = client.post("/v1/trees", json=_matrix_body(fx, negative_branch_mode="error"))
    assert resp.status_code == 500
    assert resp.json()["error"]["category"] == "computation_failure"


def test_negative_branch_report_mode_returns_events(client):
    fx = load_fixture("negative_branch.json")
    resp = client.post("/v1/trees", json=_matrix_body(fx, negative_branch_mode="report"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["newick"] == fx["expected"]["newick_report"]
    assert len(body["negative_branch_events"]) == 2


def test_get_run_returns_provenance(client):
    fx = load_fixture("additive4.json")
    run_id = client.post("/v1/trees", json=_matrix_body(fx)).json()["run_id"]
    resp = client.get(f"/v1/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["status"] == "completed"
    assert len(body["steps"]) == 2
    assert body["result"]["newick"] == fx["expected"]["newick"]


def test_get_unknown_run_maps_to_404(client):
    resp = client.get("/v1/runs/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "input_error"


def test_replay_endpoint(client):
    fx = load_fixture("additive4.json")
    run_id = client.post("/v1/trees", json=_matrix_body(fx)).json()["run_id"]
    resp = client.post(f"/v1/runs/{run_id}/replay")
    assert resp.status_code == 200
    assert resp.json()["match"] is True


def test_validate_endpoint_reports_without_building(client):
    ok = client.post("/v1/matrices:validate", json={
        "labels": ["A", "B", "C"], "values": [[0, 1, 2], [1, 0, 3], [2, 3, 0]]})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True, "violations": []}

    bad = client.post("/v1/matrices:validate", json={
        "labels": ["A", "B", "C"], "values": [[1, -2, 3], [-2, 0, 4], [9, 4, 0]]})
    assert bad.status_code == 200
    body = bad.json()
    assert body["valid"] is False
    assert [v["check"] for v in body["violations"]] == [
        "non_negative", "zero_diagonal", "symmetric"]
