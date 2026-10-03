"""Provenance store tests against an isolated SQLite database."""

from app.provenance import ProvenanceStore


def test_record_and_fetch_success(tmp_path):
    store = ProvenanceStore(str(tmp_path / "prov.db"))
    store.record_run(
        request_id="req-1",
        sample_id="s",
        input_sha256="ab" * 32,
        status="success",
        result={"total_mec": 0},
    )
    record = store.get_run("req-1")
    assert record["request_id"] == "req-1"
    assert record["status"] == "success"
    assert record["result_json"] == {"total_mec": 0}
    assert record["error_json"] is None
    assert record["app_version"]
    assert record["algorithm_version"]
    assert record["numpy_version"]
    store.close()


def test_record_and_fetch_failure(tmp_path):
    store = ProvenanceStore(str(tmp_path / "prov.db"))
    store.record_run(
        request_id="req-2",
        sample_id="s",
        input_sha256="cd" * 32,
        status="failed",
        failure_category="INVALID_REFERENCE_ALLELE",
        error={"category": "INVALID_REFERENCE_ALLELE", "message": "bad ref"},
    )
    record = store.get_run("req-2")
    assert record["failure_category"] == "INVALID_REFERENCE_ALLELE"
    assert record["result_json"] is None
    assert record["error_json"]["category"] == "INVALID_REFERENCE_ALLELE"
    store.close()


def test_missing_run_returns_none(tmp_path):
    store = ProvenanceStore(str(tmp_path / "prov.db"))
    assert store.get_run("nope") is None
    store.close()


def test_records_persist_across_connections(tmp_path):
    path = str(tmp_path / "prov.db")
    ProvenanceStore(path).record_run(
        request_id="req-3", sample_id="s", input_sha256="ef" * 32,
        status="success", result={},
    )
    assert ProvenanceStore(path).get_run("req-3")["sample_id"] == "s"
