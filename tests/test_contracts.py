"""Contract (schema) validation tests: rejection categories, not just calls."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from wsola_backend.contracts import TimeStretchRequest


def valid_payload() -> dict:
    return {"sample_rate": 16_000, "rate": 1.5, "samples": [0.0] * 2048}


def test_valid_minimal_request_accepted():
    req = TimeStretchRequest(**valid_payload())
    assert req.request_id is None
    assert req.rate == 1.5


@pytest.mark.parametrize("bad_rate", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_rate_rejected(bad_rate):
    with pytest.raises(ValidationError):
        TimeStretchRequest(**{**valid_payload(), "rate": bad_rate})


def test_non_finite_sample_rejected():
    payload = valid_payload()
    payload["samples"] = [0.0, float("nan"), 0.0]
    with pytest.raises(ValidationError):
        TimeStretchRequest(**payload)


def test_empty_samples_rejected():
    with pytest.raises(ValidationError):
        TimeStretchRequest(**{**valid_payload(), "samples": []})


@pytest.mark.parametrize("bad_sr", [0, -1, 500_000])
def test_bad_sample_rate_rejected(bad_sr):
    with pytest.raises(ValidationError):
        TimeStretchRequest(**{**valid_payload(), "sample_rate": bad_sr})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        TimeStretchRequest(**{**valid_payload(), "loudness": 1.0})
