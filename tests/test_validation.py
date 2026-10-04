"""Tests for the independent validation ledger.

The frozen hand-authored cases are run end-to-end. We additionally inject a
deliberately wrong expectation to prove a mismatch is reported as FAIL with an
explicit reason - never coerced to PASS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.schemas import ValidationExpectation
from app.services.validation import (
    independent_neutral_mass,
    validate_expectations,
)

pytestmark = pytest.mark.integration

FIXTURE = Path("tests/expected/expected_cases.json")


def _load_expectations(settings) -> list[ValidationExpectation]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [ValidationExpectation(**case) for case in data["cases"]]


def test_all_frozen_cases_pass(settings, store) -> None:
    expectations = _load_expectations(settings)
    report = validate_expectations(expectations, settings=settings, store=store)
    assert report["status"] == "PASS", [
        c["mismatches"] for c in report["cases"] if c["verdict"] == "FAIL"
    ]
    assert report["total"] == len(expectations)
    assert report["failed"] == 0
    # Every case is tied to a persisted run id for provenance.
    assert all(c["run_id"] for c in report["cases"])


def test_each_frozen_case_name_present(settings, store) -> None:
    expectations = _load_expectations(settings)
    report = validate_expectations(expectations, settings=settings, store=store)
    names = {c["case_name"] for c in report["cases"]}
    expected_names = {
        "trypsin_basic_mc0",
        "trypsin_mc1_spans",
        "trypsin_consecutive_sites_mc0",
        "trypsin_blocked_by_proline",
        "aspn_nterminal_d_gives_empty_segment",
        "trypsin_cterminal_k_gives_empty_segment",
        "ambiguous_B_mass_uncertain",
        "ambiguous_J_isobaric_mass_determinate",
    }
    assert expected_names <= names


def test_wrong_boundary_expectation_is_fail_not_success(settings, store) -> None:
    bad = ValidationExpectation(
        case_name="deliberately_wrong_boundary",
        sequence="AAKAAAKAA",
        enzyme="trypsin",
        missed_cleavages=0,
        fragment_count=99,  # impossible
        fragments=[
            {"start": 0, "end": 9, "sequence": "WRONG", "empty": False,
             "missed_cleavages": 0, "n_terminal": True, "c_terminal": True}
        ],
    )
    report = validate_expectations([bad], settings=settings, store=store)
    assert report["status"] == "FAIL"
    case = report["cases"][0]
    assert case["verdict"] == "FAIL"
    assert any("fragment_count" in m for m in case["mismatches"])
    assert any("fragments" in m or "fragment[0]" in m for m in case["mismatches"])


def test_wrong_cleavage_bond_expectation_is_fail(settings, store) -> None:
    bad = ValidationExpectation(
        case_name="deliberately_wrong_bond",
        sequence="AKKA",
        enzyme="trypsin",
        missed_cleavages=0,
        cleavage_bonds=[1],  # actual are 2,3
    )
    report = validate_expectations([bad], settings=settings, store=store)
    assert report["status"] == "FAIL"
    assert any("cleavage_bonds" in m for m in report["cases"][0]["mismatches"])


def test_aggregate_is_fail_when_any_case_fails(settings, store) -> None:
    good = ValidationExpectation(
        case_name="good", sequence="AK", enzyme="trypsin",
        missed_cleavages=0, fragment_count=2,
    )
    bad = ValidationExpectation(
        case_name="bad", sequence="AK", enzyme="trypsin",
        missed_cleavages=0, fragment_count=99,
    )
    report = validate_expectations([good, bad], settings=settings, store=store)
    assert report["status"] == "FAIL"
    assert report["passed"] == 1 and report["failed"] == 1


def test_independent_mass_oracle_matches_frozen_constants() -> None:
    # The oracle itself must agree with independently published values.
    assert independent_neutral_mass("G") == pytest.approx(75.032028404, abs=1e-6)
    assert independent_neutral_mass("AAK") == pytest.approx(288.179755262, abs=1e-6)


def test_expectation_with_unsupported_residue_is_recorded_as_fail(settings, store) -> None:
    bad = ValidationExpectation(
        case_name="illegal_residue",
        sequence="AK!A",
        enzyme="trypsin",
        missed_cleavages=0,
    )
    report = validate_expectations([bad], settings=settings, store=store)
    # The processing error is captured as a FAIL case, not raised/coerced.
    assert report["status"] == "FAIL"
    case = report["cases"][0]
    assert case["run_id"] is None
    assert any("UNSUPPORTED_RESIDUE" in m for m in case["mismatches"])


def test_validation_accepts_existing_run_id(settings, store) -> None:
    from app.api.schemas import DigestRequest
    from app.services.orchestrator import run_digest

    created = run_digest(
        DigestRequest(sequence="AAKAAAKAA", enzyme="trypsin", missed_cleavages=0),
        settings=settings,
        store=store,
    )
    expectation = ValidationExpectation(
        case_name="against_existing_run",
        sequence="AAKAAAKAA",
        enzyme="trypsin",
        missed_cleavages=0,
        fragment_count=3,
        cleavage_bonds=[3, 7],
    )
    report = validate_expectations(
        [expectation], settings=settings, store=store
    )
    assert report["status"] == "PASS"
    # The referenced run genuinely exists in the store.
    assert store.get_run(created["run_id"]) is not None


def test_fragment_mass_reference_disagreeing_with_oracle_is_fail(settings, store) -> None:
    # A frozen "expected" mass that contradicts the independent oracle is an
    # authoring error and must be flagged, not silently accepted.
    bad = ValidationExpectation(
        case_name="bad_reference_mass",
        sequence="G",
        enzyme="cnbr",
        missed_cleavages=0,
        fragment_count=1,
        fragment_masses={"G": 999.0},  # true neutral mass ~75.032
    )
    report = validate_expectations([bad], settings=settings, store=store)
    assert report["status"] == "FAIL"
    mismatch_text = " ".join(report["cases"][0]["mismatches"])
    assert "oracle" in mismatch_text
