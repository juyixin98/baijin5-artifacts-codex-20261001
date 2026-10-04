"""Service-layer tests: categorized failures, failure persistence, versions."""

from __future__ import annotations

import pytest

from app.errors import (
    EmptySequenceError,
    EnzymeNotFoundError,
    IllegalSymbolError,
    InvalidMissedCleavageError,
    RunNotFoundError,
    SequenceTooLongError,
)


def test_success_envelope_stamps_versions_and_run_id(service):
    out = service.run_digest("AAKAAA", "trypsin_syn", 0, run_id="run-fixed-1")
    assert out["success"] is True
    assert out["run_id"] == "run-fixed-1"
    assert set(out["versions"]) == {
        "app_version", "mass_table_version", "enzyme_catalog_version"
    }
    assert out["input"]["sequence"] == "AAKAAA"
    assert [f["sequence"] for f in out["result"]["fragments"]] == ["AAK", "AAA"]


def test_unknown_enzyme_is_404_category_and_persisted(service):
    with pytest.raises(EnzymeNotFoundError) as exc:
        service.run_digest("AAK", "nopease", 0, run_id="run-bad-enzyme")
    assert exc.value.code == "ENZYME_NOT_FOUND"
    record = service.get_run("run-bad-enzyme")
    assert record["status"] == "FAILED"
    assert record["error"]["code"] == "ENZYME_NOT_FOUND"


def test_empty_sequence_failure_is_persisted_not_returned_as_success(service):
    with pytest.raises(EmptySequenceError):
        service.run_digest("   ", "trypsin_syn", 0, run_id="run-empty")
    record = service.get_run("run-empty")
    assert record["status"] == "FAILED"
    assert record["error"]["code"] == "EMPTY_SEQUENCE"


def test_illegal_symbol_failure_category(service):
    with pytest.raises(IllegalSymbolError) as exc:
        service.run_digest("AA K", "trypsin_syn", 0)
    assert exc.value.code == "ILLEGAL_SYMBOL"


def test_negative_missed_cleavage_is_rejected(service):
    with pytest.raises(InvalidMissedCleavageError) as exc:
        service.run_digest("AAK", "trypsin_syn", -1, run_id="run-neg")
    assert exc.value.code == "INVALID_MISSED_CLEAVAGE"
    assert service.get_run("run-neg")["error"]["code"] == "INVALID_MISSED_CLEAVAGE"


def test_missed_cleavages_above_ceiling_is_rejected(service):
    with pytest.raises(InvalidMissedCleavageError) as exc:
        service.run_digest("AAK", "trypsin_syn", 11)
    assert exc.value.code == "INVALID_MISSED_CLEAVAGE"


def test_sequence_length_ceiling(service, isolated_settings):
    with pytest.raises(SequenceTooLongError):
        service.run_digest("A" * (isolated_settings.max_sequence_length + 1),
                           "trypsin_syn", 0)


def test_missing_run_raises_run_not_found(service):
    with pytest.raises(RunNotFoundError) as exc:
        service.get_run("run-does-not-exist")
    assert exc.value.code == "RUN_NOT_FOUND"


def test_run_round_trip_contains_decisions_and_fragments(service):
    out = service.run_digest("AAKPAA", "trypsin_syn", 0, run_id="run-trace")
    record = service.get_run("run-trace")
    assert record["status"] == "SUCCESS"
    blocked = [d for d in record["bond_decisions"] if d["decision"] == "BLOCKED"]
    assert len(blocked) == 1
    assert blocked[0]["bond"] == 3 and blocked[0]["matched_rule"] == "not_before"
    assert [f["sequence"] for f in record["fragments"]] == ["AAKPAA"]


def test_list_runs_includes_statuses(service):
    service.run_digest("AAKAAA", "trypsin_syn", 0, run_id="run-a")
    with pytest.raises(EmptySequenceError):
        service.run_digest("", "trypsin_syn", 0, run_id="run-b")
    listed = {r["run_id"]: r["status"] for r in service.list_runs()}
    assert listed["run-a"] == "SUCCESS"
    assert listed["run-b"] == "FAILED"
