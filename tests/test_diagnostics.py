"""Tests for configuration validation, storage listing and edge branches."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.evidence import EvidenceRecord
from app.service import InferenceService
from app.stats.contracts import FailureCode


def test_settings_read_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIRTEST_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("PAIRTEST_EXACT_BUDGET", "128")
    monkeypatch.setenv("PAIRTEST_MC_DRAWS", "777")
    s = Settings.from_env()
    assert s.exact_budget == 128
    assert s.mc_draws == 777
    assert str(s.db_path).endswith("x.db")


def test_settings_reject_invalid_budget_and_alpha(monkeypatch):
    monkeypatch.setenv("PAIRTEST_EXACT_BUDGET", "0")
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv("PAIRTEST_EXACT_BUDGET", "64")
    monkeypatch.setenv("PAIRTEST_INVERSION_GRID", "-3")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PAIRTEST_MC_DRAWS", "   ")
    s = Settings.from_env()
    assert s.mc_draws == 10_000


def test_float_env_helper_validates_range():
    from app.config import _float_env

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DUMMY_ALPHA", "1.0")  # boundary excluded
    with pytest.raises(ValueError):
        _float_env("DUMMY_ALPHA", 0.05)
    monkeypatch.setenv("DUMMY_ALPHA", "0.5")
    assert _float_env("DUMMY_ALPHA", 0.05) == 0.5
    monkeypatch.undo()


def test_invalid_tau_is_distinct_failure(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    out = svc.p_value({"treated": [1, 2, 3], "control": [0, 0, 0], "tau": "huge"}, ev)
    assert out is None
    assert [f.code for f in ev.failures] == [FailureCode.INVALID_TAU.value]


def test_pvalue_alpha_validation_failure(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    assert svc.p_value({"treated": [1, 2, 3], "control": [0, 0, 0], "alpha": 0}, ev) is None
    assert ev.failures[0].code == FailureCode.INVALID_ALPHA.value


def test_non_array_inputs_report_invalid_pairs(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    assert svc.p_value({"treated": "nope", "control": [1, 2]}, ev) is None
    assert ev.failures[0].code == FailureCode.INVALID_PAIRS.value


def test_storage_lists_and_fetches_requests(settings):
    from fastapi.testclient import TestClient

    from app.api import create_app

    with TestClient(create_app(settings)) as c:
        c.post("/api/pvalue", json={"treated": [1, 2], "control": [0, 0]})
        listing = c.get("/api/requests").json()["requests"]
    assert len(listing) == 1
    assert listing[0]["endpoint"] == "/api/pvalue"
    assert listing[0]["status"] == "ok"


def test_too_few_pairs_has_dedicated_code(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    assert svc.p_value({"treated": [1], "control": [2]}, ev) is None
    assert ev.failures[0].code == FailureCode.TOO_FEW_PAIRS.value


def test_invert_requires_alpha(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    assert svc.invert({"treated": [1, 2, 3], "control": [0, 0, 0]}, ev) is None
    assert ev.failures[0].code == FailureCode.INVALID_ALPHA.value


def test_invert_prob_over_budget_is_structured_failure(small_budget_settings):
    d = [3, -1, 4, -1, 5, -9, 2, -6, 5, -3, 5, -8, 9, -7, 9, -3, 2, -3]
    svc = InferenceService(small_budget_settings)
    ev = EvidenceRecord()
    out = svc.invert({"treated": d, "control": [0] * 18, "method": "two_sided_prob", "alpha": 0.1}, ev)
    assert out is None
    assert ev.failures[0].code == FailureCode.APPROXIMATION_UNRESOLVED.value


def test_randomization_preview_respects_limit(settings):
    svc = InferenceService(settings)
    ev = EvidenceRecord()
    out = svc.randomization_preview(
        {"treated": [1, 2, 3], "control": [0, 0, 0], "preview_limit": 2}, ev
    )
    assert out["randomization_set_size"] == 8
    assert len(out["assignments"]) == 2
