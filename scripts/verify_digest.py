#!/usr/bin/env python3
"""Standalone diagnostic: hand-computed digest cases with run-correlated logs.

Runs independently of pytest: each case gets its own ``run_id``; the log shows
versions, bond-scan progress, the evidence behind every CUT/BLOCKED judgment,
and an explicit PASS/FAIL verdict with the reason. Unknown/ambiguous mass
states are asserted as failures-of-knowledge, never as success.

Exit code is 0 only when every case passes; failed/unexecuted cases are
reported and counted.

Usage:
    python3 scripts/verify_digest.py [--db data/verify.db]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import __version__  # noqa: E402
from app.config import settings  # noqa: E402
from app.domain.digestion import digest  # noqa: E402
from app.domain.rules import BUILTIN_ENZYMES  # noqa: E402
from app.logging_setup import RunLogger, configure_logging  # noqa: E402

ENZYMES = {e.name: e for e in BUILTIN_ENZYMES}

# Hand-computed expected answers (not produced by the implementation).
CASES = [
    {
        "case_id": "trypsin_basic",
        "sequence": "AAKRPA",
        "enzyme": "trypsin_syn",
        "missed_cleavages": 0,
        "expect_cut_bonds": (3,),
        "expect_blocked_bonds": (4,),
        "expect_fragments": [("AAK", 1, 3), ("RPA", 4, 6)],
    },
    {
        "case_id": "consecutive_sites_no_empties",
        "sequence": "KKKA",
        "enzyme": "trypsin_syn",
        "missed_cleavages": 0,
        "expect_cut_bonds": (1, 2, 3),
        "expect_blocked_bonds": (),
        "expect_fragments": [("K", 1, 1), ("K", 2, 2), ("K", 3, 3), ("A", 4, 4)],
    },
    {
        "case_id": "missed_cleavage_1",
        "sequence": "AAKFAKLA",
        "enzyme": "trypsin_no_proline_rule",
        "missed_cleavages": 1,
        "expect_cut_bonds": (3, 6),
        "expect_blocked_bonds": (),
        "expect_fragments": [
            ("AAK", 1, 3, 0), ("AAKFAK", 1, 6, 1),
            ("FAK", 4, 6, 0), ("FAKLA", 4, 8, 1),
            ("LA", 7, 8, 0),
        ],
    },
    {
        "case_id": "single_residue",
        "sequence": "K",
        "enzyme": "trypsin_syn",
        "missed_cleavages": 0,
        "expect_cut_bonds": (),
        "expect_blocked_bonds": (),
        "expect_fragments": [("K", 1, 1)],
    },
]

# Hand-computed mass expectations: [neutral, [M+H]+, status]
MASS_CASES = [
    ("AAK", "EXACT", 288.179755, 289.187032),
    ("G", "EXACT", 75.032028, 76.039305),
    ("AXA", "UNKNOWN", None, None),
    ("AB", "AMBIGUOUS", None, None),
]


def _check_digest_case(case: dict, run_log: RunLogger) -> tuple[bool, str]:
    result = digest(
        case["sequence"], ENZYMES[case["enzyme"]],
        case["missed_cleavages"], run_log=run_log,
    )
    if tuple(result.cut_bonds) != tuple(case["expect_cut_bonds"]):
        return False, (
            f"cut_bonds={result.cut_bonds} expected={case['expect_cut_bonds']}"
        )
    if tuple(result.blocked_bonds) != tuple(case["expect_blocked_bonds"]):
        return False, (
            f"blocked_bonds={result.blocked_bonds} "
            f"expected={case['expect_blocked_bonds']}"
        )
    expected = case["expect_fragments"]
    if case["missed_cleavages"] >= 1:
        got = [(f.sequence, f.start, f.end, f.missed_cleavages)
               for f in result.fragments]
    else:
        got = [(f.sequence, f.start, f.end) for f in result.fragments]
    if got != expected:
        return False, f"fragments={got} expected={expected}"
    if "".join(f.sequence for f in result.fragments
               if f.missed_cleavages == 0) != case["sequence"]:
        return False, "primary fragments do not tile the input"
    return True, "bonds, blocking, fragment boundaries and tiling verified"


def _check_mass_case(seq: str, status: str, neutral, mh,
                     run_log: RunLogger) -> tuple[bool, str]:
    result = digest(seq, ENZYMES["trypsin_syn"], 0, run_log=run_log)
    mass = result.fragments[0].mass
    if mass.status != status:
        return False, f"status={mass.status} expected={status}"
    if status == "EXACT":
        if mass.neutral_mass != neutral or mass.mhplus_mz != mh:
            return False, (
                f"mass={mass.neutral_mass}/{mass.mhplus_mz} "
                f"expected={neutral}/{mh}"
            )
    elif status == "UNKNOWN":
        if mass.neutral_mass is not None or mass.min_neutral_mass is not None:
            return False, "UNKNOWN mass must have null numeric fields"
    elif status == "AMBIGUOUS":
        if not (mass.min_neutral_mass < mass.max_neutral_mass):
            return False, "AMBIGUOUS mass must have a strict bounded range"
    return True, f"mass status {status} verified"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    if args.db:
        os.environ["DIGEST_DB_PATH"] = args.db

    configure_logging(settings.log_dir, "DEBUG")
    batch_id = f"verify-{uuid.uuid4().hex[:8]}"
    print(f"app_version={__version__} batch_id={batch_id}")

    failures: list[str] = []
    total = len(CASES) + len(MASS_CASES)
    executed = 0
    for index, case in enumerate(CASES, start=1):
        run_id = f"{batch_id}-d{index:02d}"
        run_log = RunLogger("verify", run_id)
        run_log.step("verify_case_start",
                     f"[{index}/{len(CASES)}] {case['case_id']}",
                     input_sequence=case["sequence"], enzyme=case["enzyme"],
                     missed_cleavages=case["missed_cleavages"])
        try:
            ok, reason = _check_digest_case(case, run_log)
        except Exception as exc:  # diagnostic script: report, do not crash
            ok, reason = False, f"unexpected {type(exc).__name__}: {exc}"
        executed += 1
        verdict = "PASS" if ok else "FAIL"
        print(f"  {verdict} {run_id} {case['case_id']}: {reason}")
        run_log.step("verify_case_verdict", f"{verdict}: {reason}",
                     verdict=verdict, reason=reason)
        if not ok:
            failures.append(run_id)

    for index, (seq, status, neutral, mh) in enumerate(MASS_CASES, start=1):
        run_id = f"{batch_id}-m{index:02d}"
        run_log = RunLogger("verify", run_id)
        run_log.step("verify_mass_start",
                     f"[mass {index}/{len(MASS_CASES)}] {seq}",
                     input_sequence=seq)
        try:
            ok, reason = _check_mass_case(seq, status, neutral, mh, run_log)
        except Exception as exc:
            ok, reason = False, f"unexpected {type(exc).__name__}: {exc}"
        executed += 1
        verdict = "PASS" if ok else "FAIL"
        print(f"  {verdict} {run_id} mass({seq}) [{status}]: {reason}")
        run_log.step("verify_mass_verdict", f"{verdict}: {reason}",
                     verdict=verdict, reason=reason)
        if not ok:
            failures.append(run_id)

    unexecuted = total - executed
    print(f"summary: total={total} executed={executed} "
          f"failed={len(failures)} unexecuted={unexecuted}")
    if failures:
        print("FAILED_RUN_IDS=" + json.dumps(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
