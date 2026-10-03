"""Diagnostics redaction tests."""

from __future__ import annotations

import logging

import pytest

from txmap.diagnostics import configure_logging, log_event, redact

pytestmark = pytest.mark.unit


def test_redacts_sensitive_keys():
    payload = {
        "transcript_id": "T1",
        "api_key": "AKIA-deadbeef",
        "nested": {"password": "hunter2", "ok": 1},
        "headers": {"Authorization": "Bearer abc"},
    }
    safe = redact(payload)
    assert safe["api_key"] == "***REDACTED***"
    assert safe["nested"]["password"] == "***REDACTED***"
    assert safe["nested"]["ok"] == 1
    assert safe["headers"]["Authorization"] == "***REDACTED***"
    assert "T1" == safe["transcript_id"]


def test_long_string_truncated_but_length_hinted():
    safe = redact({"seq": "A" * 500})
    assert "500 chars" in safe["seq"]
    assert "A" * 500 != safe["seq"]


def test_log_event_emits_request_id_and_redacts(caplog):
    configure_logging("INFO")
    with caplog.at_level(logging.WARNING, logger="txmap"):
        log_event(logging.WARNING, "mapping_rejected", "req-42",
                  error_code="intronic_position", secret="shh", position=145)
    rec = caplog.records[-1]
    # identifiers and key state ride on structured record attributes
    assert rec.request_id == "req-42"
    assert rec.event == "mapping_rejected"
    assert "intronic_position" in rec.detail
    assert "145" in rec.detail
    assert "shh" not in rec.detail
    assert "REDACTED" in rec.detail
