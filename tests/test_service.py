"""Service + SQLite integration: identity isolation and audit provenance."""

from __future__ import annotations

import sqlite3

import pytest

from txmap.errors import (
    CoordinateOutOfRangeError,
    IntronicPositionError,
    RegionNotMappableError,
    TranscriptNotFoundError,
)
from txmap.storage import Repository

pytestmark = pytest.mark.integration


def test_round_trip_persistence(repo):
    tx = repo.get_transcript("T2_MINUS")
    assert tx is not None and tx.strand == "-" and tx.length == 75
    assert repo.get_pattern("syn2") == "ACGT"
    assert repo.get_transcript("NOPE") is None


def test_unknown_transcript_isolation(service):
    with pytest.raises(TranscriptNotFoundError) as exc:
        service.transcript_info("T_GHOST")
    assert exc.value.code == "transcript_not_found"
    assert "T_GHOST" in exc.value.key_state["transcript_id"]


def test_identity_isolation_same_coordinate_different_answer(service):
    # tx position 0 must resolve per transcript, never cross-contaminate
    a, _ = service.map_point("T1_PLUS", "tx_to_genomic", 0, "req-A")
    b, _ = service.map_point("T2_MINUS", "tx_to_genomic", 0, "req-B")
    assert a["result"]["genomic_position"] == 100
    assert b["result"]["genomic_position"] == 639
    assert a["result"]["strand"] != b["result"]["strand"]
    # position beyond one transcript's length is an isolated out-of-range,
    # never silently answered from another transcript's coordinates
    with pytest.raises(CoordinateOutOfRangeError):
        service.map_point("T2_MINUS", "tx_to_genomic", 79, "req-C")  # T2 only 75 long
    # and an unknown id can never resolve against another transcript's data
    with pytest.raises(TranscriptNotFoundError):
        service.map_point("T_DOES_NOT_EXIST", "tx_to_genomic", 0, "req-D")


def test_rejected_intronic_request_is_audited(service):
    with pytest.raises(IntronicPositionError):
        service.map_point("T1_PLUS", "genomic_to_tx", 145, "req-REJ")
    trail = service.audit_trail("req-REJ")
    assert len(trail) == 1
    row = trail[0]
    assert row["status"] == "rejected"
    assert row["error_code"] == "intronic_position"
    assert row["transcript_id"] == "T1_PLUS"
    assert row["input_json"] == {"position": 145}
    assert row["result_json"] is None


def test_accepted_interval_audit_holds_fragments(service):
    out, audit_id = service.map_interval(
        "T2_MINUS", "tx_to_genomic", 15, 50, "req-OK"
    )
    assert out["audit_id"] == audit_id
    row = service._repo.get_audit(audit_id)
    assert row["status"] == "mapped"
    assert row["result_json"]["fragment_count"] == 3
    assert row["result_json"]["mapped_length"] == 35


def test_region_not_mappable_audited_with_gaps(service):
    with pytest.raises(RegionNotMappableError):
        service.map_interval(
            "T1_PLUS", "genomic_to_tx", 125, 165, "req-GAP"
        )
    row = service.audit_trail("req-GAP")[0]
    assert row["status"] == "rejected"
    assert row["error_code"] == "region_not_mappable"


def test_request_ids_partition_audit_trails(service):
    service.map_point("T1_PLUS", "tx_to_genomic", 0, "group-1")
    service.map_point("T1_PLUS", "tx_to_genomic", 1, "group-1")
    service.map_point("T1_PLUS", "tx_to_genomic", 2, "group-2")
    assert len(service.audit_trail("group-1")) == 2
    assert len(service.audit_trail("group-2")) == 1


def test_list_transcripts_exposes_fixture(service):
    ids = [t["transcript_id"] for t in service.list_transcripts()]
    assert ids == ["T1_PLUS", "T2_MINUS", "T3_ADJACENT"]


def test_unknown_audit_id_returns_none(service):
    assert service._repo.get_audit("deadbeef") is None
    assert service.audit_trail("never-seen") == []


def test_repository_context_manager_and_direct_audit_query(tmp_path, reference, patterns):
    path = tmp_path / "ctx.sqlite3"
    with Repository(path) as r:
        r.replace_reference(reference[0], patterns, reference[1])
        assert r.get_transcript("T1_PLUS") is not None
    # connection is closed after the with-block
    with pytest.raises(sqlite3.ProgrammingError):
        r.list_transcript_ids()
