"""Integration tests for the FastAPI validation surface.

Assertions cover concrete results and failure categories -- not merely
that endpoints respond.
"""

import pytest
from conftest import judgement, load_expected, read_fixture
from fastapi.testclient import TestClient

from msa_backend.api.app import create_app

TOL = 1e-9


@pytest.fixture()
def client(config, store):
    app = create_app(config=config, store=store)
    return TestClient(app, raise_server_exceptions=False)


def test_health_reports_versions(client):
    response = client.get("/health")
    judgement("health", "api", "liveness payload carries component versions")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["versions"]) == {"msa_backend", "python", "numpy"}


def test_post_run_returns_reference_matching_results(client):
    expected = load_expected("conserved")
    response = client.post(
        "/v1/runs", json={"fasta": read_fixture("conserved.fa"), "label": "it-conserved"}
    )
    judgement(
        "conserved.fa", "api-post-run",
        "201 with per-column values equal to static reference JSON",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["run"]["label"] == "it-conserved"
    assert body["run"]["n_sequences"] == 4
    assert body["run"]["n_columns"] == 12
    assert body["run"]["total_weight"] == pytest.approx(4.0)
    for got, want in zip(body["columns"], expected["columns"]):
        assert got["entropy_bits"] == pytest.approx(want["entropy_bits"], abs=TOL)
        assert got["consensus"] == want["consensus"]
        assert got["status"] == want["status"]


def test_get_run_roundtrip_and_columns_endpoint(client):
    created = client.post(
        "/v1/runs", json={"fasta": read_fixture("gappy.fa"), "label": "it-gappy"}
    ).json()
    run_id = created["run"]["run_id"]

    detail = client.get(f"/v1/runs/{run_id}")
    judgement(f"run={run_id}", "api-get-run", "persisted detail matches POST response")
    assert detail.status_code == 200
    assert detail.json()["columns"] == created["columns"]
    assert detail.json()["coordinate_maps"]["s2"] == [1, 2, None, None, 3, 4, 5, 6]

    columns = client.get(f"/v1/runs/{run_id}/columns")
    assert columns.status_code == 200
    insufficient = [c for c in columns.json() if c["status"] == "insufficient_coverage"]
    judgement(
        f"run={run_id}", "api-columns",
        "gappy columns 3-4 must surface as insufficient_coverage via the API",
    )
    assert [c["column_index"] for c in insufficient] == [3, 4]


def test_unknown_run_returns_404_with_category(client):
    response = client.get("/v1/runs/doesnotexist")
    judgement("run=doesnotexist", "api-404", "unknown run id -> 404 run_not_found, not 200")
    assert response.status_code == 404
    assert response.json()["error"]["category"] == "run_not_found"


def test_invalid_fasta_returns_422_with_category(client):
    response = client.post("/v1/runs", json={"fasta": "not fasta at all", "label": "bad"})
    judgement("invalid-input", "api-422", "unparseable input -> 422 fasta_parse_error")
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "fasta_parse_error"


def test_ragged_alignment_returns_422_shape_category(client):
    response = client.post(
        "/v1/runs", json={"fasta": ">a\nACGT\n>b\nACG\n", "label": "ragged"}
    )
    judgement("ragged-input", "api-422", "non-rectangular alignment -> alignment_shape_error")
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "alignment_shape_error"


def test_failed_run_is_recorded_as_failed_not_success(client, store):
    client.post("/v1/runs", json={"fasta": "garbage", "label": "will-fail"})
    runs = store.list_runs()
    judgement(
        "failed-run", "provenance",
        "a rejected input leaves a status=failed row with an error category",
    )
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_category"] == "fasta_parse_error"


def test_duplicate_evidence_not_amplified_via_api(client):
    dup = client.post(
        "/v1/runs", json={"fasta": read_fixture("duplicates.fa"), "label": "dup"}
    ).json()
    pair = client.post(
        "/v1/runs", json={"fasta": read_fixture("duplicates_pair.fa"), "label": "pair"}
    ).json()
    dup_col8 = dup["columns"][7]
    pair_col8 = pair["columns"][7]
    judgement(
        f"runs {dup['run']['run_id']} vs {pair['run']['run_id']}", "api-weighting",
        "3 identical copies + variant must match 1 copy + variant at column 8",
    )
    assert dup["run"]["total_weight"] == pytest.approx(2.0)
    assert dup_col8["entropy_bits"] == pytest.approx(pair_col8["entropy_bits"], abs=TOL)
    assert dup_col8["entropy_bits"] == pytest.approx(1.0, abs=TOL)
    assert dup_col8["distribution"]["T"] == pytest.approx(0.5, abs=TOL)
