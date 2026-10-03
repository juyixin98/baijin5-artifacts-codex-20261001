"""API tests: responses carry request identity, versions, evidence and
categorized failures — not just a 200/422 status."""

from conftest import load_fixture


def test_health_and_version(client):
    assert client.get("/v1/health").json() == {"status": "ok"}
    version = client.get("/v1/version").json()
    assert version["app"]
    assert version["algorithm"].startswith("mec-exact-enum")
    assert version["numpy"]


def test_phase_basic_clean_end_to_end(client):
    response = client.post("/v1/phase", json=load_fixture("basic_clean"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["request_id"]
    assert body["versions"]["algorithm"]
    assert len(body["input_sha256"]) == 64

    result = body["result"]
    assert result["sample_id"] == "synthetic-basic-clean"
    assert result["n_blocks"] == 1
    block = result["blocks"][0]
    assert block["haplotype1"] == ["A", "C", "G"]
    assert block["haplotype2"] == ["G", "T", "A"]
    assert block["mec"] == 0
    assert block["ambiguous"] is False


def test_phase_error_read_reports_conflict_evidence(client):
    body = client.post("/v1/phase", json=load_fixture("error_reads")).json()
    block = body["result"]["blocks"][0]
    assert block["mec"] == 25
    assert block["conflicting_reads"] == 1
    err = next(a for a in block["read_assignments"] if a["read_id"] == "r_err")
    assert err["correction_cost"] == 25
    assert err["corrections"][0]["site_id"] == "s1"


def test_phase_ambiguous_flags_uncertainty(client):
    body = client.post("/v1/phase", json=load_fixture("ambiguous")).json()
    block = body["result"]["blocks"][0]
    assert block["ambiguous"] is True
    assert block["n_optima"] == 2
    assert body["result"]["uncertainties"], "ambiguous phase must be listed as uncertain"


def test_phase_disconnected_outputs_separate_blocks(client):
    body = client.post("/v1/phase", json=load_fixture("disconnected")).json()
    result = body["result"]
    assert result["n_blocks"] == 3
    site_sets = [tuple(b["site_ids"]) for b in result["blocks"]]
    assert site_sets == [("s1", "s2"), ("s3", "s4"), ("s5",)]


def test_invalid_reference_allele_returns_categorized_422(client):
    payload = load_fixture("basic_clean")
    payload["sites"][0]["ref"] = "N"
    response = client.post("/v1/phase", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["category"] == "INVALID_REFERENCE_ALLELE"
    assert body["error"]["context"]["site_id"] == "s1"
    assert body["request_id"]


def test_read_referencing_unknown_site_returns_categorized_422(client):
    payload = load_fixture("basic_clean")
    payload["reads"][0]["calls"][0]["site"] = "sX"
    response = client.post("/v1/phase", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "READ_REFERENCES_UNKNOWN_SITE"


def test_quality_out_of_range_returns_categorized_422(client):
    payload = load_fixture("basic_clean")
    payload["reads"][0]["calls"][0]["qual"] = 99
    response = client.post("/v1/phase", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "QUALITY_OUT_OF_RANGE"


def test_fixture_endpoint_phases_bundled_dataset(client):
    response = client.post("/v1/phase/fixture/basic_clean")
    assert response.status_code == 200
    assert response.json()["result"]["blocks"][0]["mec"] == 0


def test_fixture_endpoint_rejects_unknown_and_traversal_names(client):
    assert client.post("/v1/phase/fixture/does_not_exist").status_code == 404
    assert client.post("/v1/phase/fixture/..%2F..%2Fconfig%2Fdefault").status_code == 404


def test_provenance_record_roundtrip(client):
    created = client.post("/v1/phase", json=load_fixture("basic_clean")).json()
    record = client.get(f"/v1/runs/{created['request_id']}")
    assert record.status_code == 200
    record = record.json()
    assert record["status"] == "success"
    assert record["sample_id"] == "synthetic-basic-clean"
    assert record["input_sha256"] == created["input_sha256"]
    assert record["algorithm_version"] == created["versions"]["algorithm"]
    assert record["result_json"]["total_mec"] == 0
    assert record["created_at"]


def test_failed_request_is_recorded_with_category(client):
    payload = load_fixture("basic_clean")
    payload["sites"][1]["alt"] = "C"  # equals ref
    failed = client.post("/v1/phase", json=payload).json()
    assert failed["error"]["category"] == "INVALID_ALT_ALLELE"
    record = client.get(f"/v1/runs/{failed['request_id']}").json()
    assert record["status"] == "failed"
    assert record["failure_category"] == "INVALID_ALT_ALLELE"
    assert record["error_json"]["category"] == "INVALID_ALT_ALLELE"


def test_unknown_run_returns_404(client):
    assert client.get("/v1/runs/00000000-0000-0000-0000-000000000000").status_code == 404
