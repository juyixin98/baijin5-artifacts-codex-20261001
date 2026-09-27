"""Tests for structured diagnostics: identity, redaction, status triage."""
from __future__ import annotations

import numpy as np

from autodiff.config import Config
from autodiff.diagnostics import (
    ACCEPTED,
    REJECTED,
    UNABLE,
    Diagnostics,
    redact,
    redact_array,
    new_record_id,
)


def test_records_carry_request_and_record_identity():
    diag = Diagnostics(request_id="req-xyz", config=Config(log_diagnostics=False))
    rec = diag.accepted("test.event", "reason text")
    assert rec.request_id == "req-xyz"
    assert rec.record_id.startswith("rec-")
    assert rec.status == ACCEPTED
    assert rec.reason == "reason text"
    assert rec.event == "test.event"


def test_record_ids_are_unique():
    diag = Diagnostics(request_id="r", config=Config(log_diagnostics=False))
    ids = {diag.accepted("e", "r").record_id for _ in range(5)}
    assert len(ids) == 5


def test_status_counts():
    diag = Diagnostics(request_id="r", config=Config(log_diagnostics=False))
    diag.accepted("a", "ok")
    diag.rejected("b", "bad")
    diag.rejected("c", "bad2")
    diag.unable("d", "unknown")
    counts = diag.status_counts()
    assert counts == {ACCEPTED: 1, REJECTED: 2, UNABLE: 1}


def test_invalid_status_rejected_at_emit():
    diag = Diagnostics(request_id="r", config=Config(log_diagnostics=False))
    try:
        diag.emit("e", "MAYBE", "reason")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid status must raise")


def test_redact_small_array_shows_preview():
    summary = redact_array(np.array([1.0, 2.0, 3.0]))
    assert summary["shape"] == [3]
    assert summary["preview"] == [1.0, 2.0, 3.0]
    assert summary["finite_count"] == 3
    assert summary["nonfinite_count"] == 0


def test_redact_large_array_hides_values_keeps_stats():
    big = np.arange(100.0)
    summary = redact_array(big)
    assert "preview" not in summary
    assert "head" in summary
    assert len(summary["head"]) == 8
    assert summary["min"] == 0.0
    assert summary["max"] == 99.0
    assert summary["size"] == 100


def test_redact_empty_array():
    summary = redact_array(np.zeros((0, 2)))
    assert summary["shape"] == [0, 2]
    assert summary["preview"] == []


def test_redact_nonfinite_count():
    summary = redact_array(np.array([1.0, np.nan, np.inf]))
    assert summary["finite_count"] == 1
    assert summary["nonfinite_count"] == 2


def test_redact_long_string_is_masked():
    out = redact("x" * 100)
    assert "xxxx" not in out
    assert "redacted" in out


def test_redact_nested_structure():
    out = redact({"a": np.ones(2), "b": [1, 2, 3], "c": np.arange(20.0)})
    assert out["a"]["preview"] == [1.0, 1.0]
    assert out["b"] == [1, 2, 3]
    assert "head" in out["c"]


def test_to_dict_redacts_state():
    diag = Diagnostics(request_id="r", config=Config(log_diagnostics=False))
    rec = diag.rejected("e", "why", big=np.arange(50.0), secret="s" * 40)
    payload = rec.to_dict()
    assert payload["state"]["big"]["shape"] == [50]
    assert "head" in payload["state"]["big"]
    assert "redacted" in payload["state"]["secret"]
    assert payload["request_id"] == "r"
