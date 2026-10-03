"""Diagnostics: correlation ids, rationale, redaction of sensitive data."""

from depthcov.diagnostics import (
    malformed_event,
    redact,
    redact_mapping,
    verdict_event,
)
from depthcov.filtering import FilterConfig, adjudicate
from depthcov.models import Alignment


def test_redaction_is_non_reversible_but_carries_shape():
    secret = "PATIENT-1234567890-ACGT"
    out = redact(secret)
    assert "PATIENT-1234567890-ACGT" not in out
    assert "len=23" in out
    assert "sha256=" in out
    # Same input -> same fingerprint; different -> different.
    assert redact(secret) == redact(secret)
    assert redact(secret) != redact(secret + "x")


def test_redact_mapping_handles_nested_sensitive_keys():
    payload = {
        "ref": "chr1",
        "sample_id": "donor-x-secret",
        "nested": {"seq": "ACGTACGT", "ok": 1},
    }
    safe = redact_mapping(payload)
    assert safe["ref"] == "chr1"
    assert "donor-x-secret" not in str(safe)
    assert "ACGTACGT" not in str(safe)
    assert safe["nested"]["ok"] == 1


def test_accepted_verdict_event_carries_key_state():
    v = adjudicate(
        Alignment("q9", "chr1", 3, "3M2D2M", mapq=60),
        FilterConfig(), ref_length=20, known_refs={"chr1"},
    )
    event = verdict_event(v, request_id="req-42").as_dict()
    assert event["request_id"] == "req-42"
    assert event["record_id"] == "q9"
    assert event["outcome"] == "accepted"
    assert event["state"]["ref"] == "chr1"
    assert event["state"]["ref_start"] == 3
    assert event["state"]["ref_end"] == 10
    assert event["state"]["covered_bases"] == 5  # gap excluded
    assert event["state"]["block_count"] == 2


def test_rejected_event_states_why():
    v = adjudicate(
        Alignment("q", "chr1", 0, "5M", mapq=3),
        FilterConfig(min_mapq=20), ref_length=20, known_refs={"chr1"},
    )
    event = verdict_event(v, request_id="r").as_dict()
    assert event["outcome"] == "rejected"
    assert event["reason"] == "low_mapq"
    assert "below minimum 20" in event["message"]


def test_malformed_row_is_undetermined_not_silent():
    event = malformed_event(
        "garbage\tline", ValueError("boom"),
        request_id="r", record_index=7,
    ).as_dict()
    assert event["outcome"] == "undetermined"
    assert event["record_id"] == "row#7"
    assert event["reason"] == "malformed_record"
    assert "garbage" not in str(event["state"])  # raw content redacted
