"""Integration tests: SQLite provenance persistence and retrieval."""

from __future__ import annotations

import pytest

from app.api.schemas import DigestRequest
from app.services.orchestrator import run_digest

pytestmark = pytest.mark.integration


def _digest(settings, store, sequence="AAKAAAKAA", enzyme="trypsin", **overrides):
    request = DigestRequest(sequence=sequence, enzyme=enzyme, **overrides)
    return run_digest(request, settings=settings, store=store)


def test_run_and_fragments_are_persisted(settings, store) -> None:
    response = _digest(settings, store)
    run_id = response["run_id"]
    stored = store.get_run(run_id)
    assert stored is not None
    assert stored["sequence"] == "AAKAAAKAA"
    assert stored["enzyme_key"] == "trypsin"
    assert stored["fragment_count"] == 3
    assert len(stored["fragments"]) == 3
    first = stored["fragments"][0]
    assert first["sequence"] == "AAK"
    assert first["start_pos"] == 0 and first["end_pos"] == 3
    assert first["mass_status"] == "DETERMINATE"


def test_get_missing_run_returns_none(settings, store) -> None:
    assert store.get_run("run-does-not-exist") is None


def test_list_runs_orders_newest_first(settings, store) -> None:
    first = _digest(settings, store, "AK")
    second = _digest(settings, store, "AKA")
    runs = store.list_runs()
    ids = [r["run_id"] for r in runs]
    assert ids.index(second["run_id"]) < ids.index(first["run_id"])


def test_uncertain_mass_status_is_persisted_distinctly(settings, store) -> None:
    response = _digest(settings, store, "ABG", enzyme="cnbr")
    stored = store.get_run(response["run_id"])
    assert stored["mass_uncertain"] == 1
    assert stored["fragments"][0]["mass_status"] == "UNCERTAIN"


def test_validation_record_is_persisted(settings, store) -> None:
    from app.api.schemas import ValidationExpectation
    from app.services.validation import validate_expectations

    expectation = ValidationExpectation(
        case_name="persist-check",
        sequence="AK",
        enzyme="trypsin",
        missed_cleavages=0,
        fragment_count=2,
    )
    report = validate_expectations([expectation], settings=settings, store=store)
    validation_id = f"{report['validation_id']}:0"
    record = store.get_validation(validation_id)
    assert record is not None
    assert record["status"] == "PASS"
    assert record["case_name"] == "persist-check"
    assert record["run_id"] is not None
