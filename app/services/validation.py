"""Independent validation ledger.

Expected outcomes are *authored independently* of the digest engine:
test/reference data supplies literal fragment boundaries, sequences and
cleavage bonds. Neutral masses are cross-checked by a second, independent
calculation built directly from integer residue formulas (NumPy), not by
calling the engine's own mass routine.

A mismatch never becomes a success: each case yields PASS/FAIL with explicit
reason strings, and the aggregate status is FAIL if any case fails.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import __version__
from app.api.schemas import DigestRequest, ValidationExpectation
from app.domain.constants import ELEMENT_MASSES, ELEMENT_ORDER, RESIDUES, WATER_MASS
from app.domain.errors import DigestError
from app.logging_config import StepTimer, get_logger
from app.services.orchestrator import run_digest
from app.storage.store import DigestStore

import numpy as np


# ---------------------------------------------------------------------------
# Independent mass oracle: integer formula vectors -> mass, via NumPy.
# Deliberately separate from app.services.mass / Residue.residue_mass.
# ---------------------------------------------------------------------------

_ELEMENT_INDEX = {element: i for i, element in enumerate(ELEMENT_ORDER)}


def _independent_residue_mass(letter: str) -> float:
    residue = RESIDUES[letter]
    vector = np.zeros(len(ELEMENT_ORDER), dtype=np.float64)
    for element, count in zip(ELEMENT_ORDER, residue.formula):
        vector[_ELEMENT_INDEX[element]] += count
    vector[_ELEMENT_INDEX["H"]] -= 2
    vector[_ELEMENT_INDEX["O"]] -= 1
    atomic = np.array([ELEMENT_MASSES[e] for e in ELEMENT_ORDER], dtype=np.float64)
    return float(np.dot(vector, atomic))


def independent_neutral_mass(sequence: str) -> float:
    """sum(residue) + H2O using the independent NumPy oracle."""
    total = float(WATER_MASS)
    for letter in sequence:
        total += _independent_residue_mass(letter)
    return total


MASS_TOLERANCE = 1e-6


def _new_validation_id() -> str:
    return "val-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _request_for(expectation: ValidationExpectation) -> DigestRequest:
    return DigestRequest(
        sequence=expectation.sequence,
        enzyme=expectation.enzyme,
        custom_rule=expectation.custom_rule,
        missed_cleavages=expectation.missed_cleavages,
    )


def validate_expectations(
    expectations: list[ValidationExpectation],
    *,
    settings,
    store: DigestStore | None,
) -> dict[str, Any]:
    logger = get_logger()
    validation_id = _new_validation_id()
    case_views: list[dict[str, Any]] = []
    passed = 0

    logger.info(
        "validation started",
        extra={
            "extra_fields": {
                "event": "validation_start",
                "validation_id": validation_id,
                "case_count": len(expectations),
                "service_version": __version__,
            }
        },
    )

    for order, expectation in enumerate(expectations):
        mismatches: list[str] = []
        run_id: str | None = None
        fragment_count = 0

        with StepTimer(
            "validate_case",
            case_name=expectation.case_name,
            case_index=order,
            validation_id=validation_id,
        ):
            try:
                request = _request_for(expectation)
                response = run_digest(request, settings=settings, store=store)
                run_id = response["run_id"]
                fragment_count = response["fragment_count"]

                if expectation.fragment_count is not None:
                    if fragment_count != expectation.fragment_count:
                        mismatches.append(
                            f"fragment_count: expected {expectation.fragment_count}, "
                            f"got {fragment_count}"
                        )

                actual_bonds = sorted(s["bond"] for s in response["cleavage_sites"])
                if expectation.cleavage_bonds is not None:
                    expected_bonds = sorted(expectation.cleavage_bonds)
                    if actual_bonds != expected_bonds:
                        mismatches.append(
                            f"cleavage_bonds: expected {expected_bonds}, got {actual_bonds}"
                        )

                actual_blocked = sorted(s["bond"] for s in response["blocked_sites"])
                if expectation.blocked_bonds is not None:
                    expected_blocked = sorted(expectation.blocked_bonds)
                    if actual_blocked != expected_blocked:
                        mismatches.append(
                            f"blocked_bonds: expected {expected_blocked}, got {actual_blocked}"
                        )

                if expectation.has_ambiguous is not None:
                    if response["has_ambiguous"] != expectation.has_ambiguous:
                        mismatches.append(
                            f"has_ambiguous: expected {expectation.has_ambiguous}, "
                            f"got {response['has_ambiguous']}"
                        )

                if expectation.mass_uncertain is not None:
                    if response["mass_uncertain"] != expectation.mass_uncertain:
                        mismatches.append(
                            f"mass_uncertain: expected {expectation.mass_uncertain}, "
                            f"got {response['mass_uncertain']}"
                        )

                # Literal, independently-authored fragment boundary expectations.
                if expectation.fragments is not None:
                    actual_fragments = [
                        {
                            "start": f["start"],
                            "end": f["end"],
                            "sequence": f["sequence"],
                            "empty": f["empty"],
                            "missed_cleavages": f["missed_cleavages"],
                            "n_terminal": f["n_terminal"],
                            "c_terminal": f["c_terminal"],
                        }
                        for f in response["fragments"]
                    ]
                    expected_fragments = expectation.fragments
                    if len(actual_fragments) != len(expected_fragments):
                        mismatches.append(
                            f"fragments: expected {len(expected_fragments)} entries, "
                            f"got {len(actual_fragments)}"
                        )
                    else:
                        for idx, (exp, act) in enumerate(
                            zip(expected_fragments, actual_fragments)
                        ):
                            for key in (
                                "start",
                                "end",
                                "sequence",
                                "empty",
                                "missed_cleavages",
                                "n_terminal",
                                "c_terminal",
                            ):
                                if key in exp and exp[key] != act[key]:
                                    mismatches.append(
                                        f"fragment[{idx}].{key}: expected "
                                        f"{exp[key]!r}, got {act[key]!r}"
                                    )

                # Independent NumPy mass cross-check for determinate fragments.
                if expectation.fragment_masses is not None:
                    by_sequence: dict[str, list[dict[str, Any]]] = {}
                    for fragment in response["fragments"]:
                        by_sequence.setdefault(fragment["sequence"], []).append(fragment)
                    for seq, expected_mass in expectation.fragment_masses.items():
                        matches = by_sequence.get(seq, [])
                        if not matches:
                            mismatches.append(
                                f"fragment_masses: fragment {seq!r} not present"
                            )
                            continue
                        oracle_mass = independent_neutral_mass(seq)
                        if abs(oracle_mass - expected_mass) > MASS_TOLERANCE:
                            # Authoring error: expected value disagrees with oracle.
                            mismatches.append(
                                f"fragment_masses[{seq!r}]: authored expected "
                                f"{expected_mass:.6f} but independent oracle gives "
                                f"{oracle_mass:.6f}"
                            )
                        for fragment in matches:
                            actual_mass = fragment["mass"]["nominal"]
                            if abs(actual_mass - oracle_mass) > MASS_TOLERANCE:
                                mismatches.append(
                                    f"fragment_masses[{seq!r}] at "
                                    f"{fragment['start']}-{fragment['end']}: "
                                    f"engine {actual_mass:.6f} != oracle {oracle_mass:.6f}"
                                )
                            if fragment["mass"]["status"] != "DETERMINATE":
                                mismatches.append(
                                    f"fragment_masses[{seq!r}]: expected determinate "
                                    f"mass, got {fragment['mass']['status']}"
                                )

            except DigestError as exc:
                mismatches.append(f"unexpected domain error {exc.code.value}: {exc.message}")
                logger.error(
                    "validation case raised domain error",
                    extra={
                        "extra_fields": {
                            "event": "validation_case_error",
                            "case_name": expectation.case_name,
                            "error": exc.code.value,
                            "verdict": "FAIL",
                        }
                    },
                )

        verdict = "PASS" if not mismatches else "FAIL"
        if verdict == "PASS":
            passed += 1
        else:
            logger.error(
                "validation case failed",
                extra={
                    "extra_fields": {
                        "event": "validation_case_fail",
                        "case_name": expectation.case_name,
                        "mismatch_count": len(mismatches),
                        "mismatches": mismatches,
                        "verdict": "FAIL",
                    }
                },
            )

        case_views.append(
            {
                "case_name": expectation.case_name,
                "verdict": verdict,
                "run_id": run_id,
                "mismatches": mismatches,
                "fragment_count": fragment_count,
            }
        )

        if store is not None and run_id is not None:
            store.save_validation(
                {
                    "validation_id": f"{validation_id}:{order}",
                    "run_id": run_id,
                    "case_name": expectation.case_name,
                    "status": verdict,
                    "expected_ref": expectation.model_dump(),
                    "actual_summary": {"fragment_count": fragment_count},
                    "mismatches": mismatches,
                    "service_version": __version__,
                }
            )

    total = len(expectations)
    failed = total - passed
    overall = "PASS" if failed == 0 else "FAIL"
    logger.info(
        "validation completed",
        extra={
            "extra_fields": {
                "event": "validation_end",
                "validation_id": validation_id,
                "total": total,
                "passed": passed,
                "failed": failed,
                "verdict": overall,
            }
        },
    )

    return {
        "validation_id": validation_id,
        "status": overall,
        "service_version": __version__,
        "total": total,
        "passed": passed,
        "failed": failed,
        "cases": case_views,
    }
