"""Integration tests for the pipeline + SQLite provenance store."""

import hashlib
import json

import pytest
from conftest import judgement, load_expected, read_fixture

from msa_backend.pipeline import component_versions, run_pipeline

TOL = 1e-9


def test_run_persists_full_provenance(config, store):
    fasta = read_fixture("conserved.fa")
    run_id, result = run_pipeline(fasta, "prov-conserved", config, store)

    row = store.get_run(run_id)
    judgement(
        f"run={run_id}", "provenance-run-row",
        "run row carries status, input hash, config snapshot and versions",
    )
    assert row["status"] == "completed"
    assert row["label"] == "prov-conserved"
    assert row["input_sha256"] == hashlib.sha256(fasta.encode()).hexdigest()
    assert row["n_sequences"] == 4
    assert row["n_columns"] == 12
    assert row["total_weight"] == pytest.approx(4.0)
    assert row["finished_at"] is not None

    saved_config = json.loads(row["config_json"])
    assert saved_config["ambiguity_policy"] == "uniform_split"
    assert saved_config["min_effective_coverage"] == pytest.approx(2.0)

    saved_versions = json.loads(row["versions_json"])
    assert saved_versions == component_versions()


def test_columns_and_maps_persisted(config, store):
    expected = load_expected("gappy")
    run_id, _ = run_pipeline(read_fixture("gappy.fa"), "prov-gappy", config, store)

    columns = store.get_columns(run_id)
    judgement(
        f"run={run_id}", "provenance-columns",
        "persisted columns equal static reference values",
    )
    assert len(columns) == len(expected["columns"])
    for got, want in zip(columns, expected["columns"]):
        assert got["entropy_bits"] == pytest.approx(want["entropy_bits"], abs=TOL)
        assert got["status"] == want["status"]
        assert got["consensus"] == want["consensus"]

    maps = store.get_coordinate_maps(run_id)
    assert maps["s2"] == [1, 2, None, None, 3, 4, 5, 6]

    weights = {w["sequence_id"]: w["weight"] for w in store.get_weights(run_id)}
    assert weights == {"s1": 0.5, "s2": 1.0, "s3": 1.0, "s4": 0.5}


def test_failed_run_marks_failure_with_category(config, store):
    from msa_backend.errors import FastaParseError

    with pytest.raises(FastaParseError):
        run_pipeline("definitely not fasta", "prov-fail", config, store)
    runs = store.list_runs()
    judgement(
        "failed-run", "provenance-failure",
        "exception path leaves status=failed + category, never a fake success",
    )
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_category"] == "fasta_parse_error"
    assert runs[0]["finished_at"] is not None


def test_same_input_same_hash_distinct_run_ids(config, store):
    fasta = read_fixture("diverse.fa")
    run_a, _ = run_pipeline(fasta, "a", config, store)
    run_b, _ = run_pipeline(fasta, "b", config, store)
    judgement(
        f"runs {run_a},{run_b}", "provenance-identity",
        "identical input -> identical sha256, distinct run identities",
    )
    assert run_a != run_b
    assert store.get_run(run_a)["input_sha256"] == store.get_run(run_b)["input_sha256"]
