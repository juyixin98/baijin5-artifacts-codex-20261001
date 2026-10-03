#!/usr/bin/env python3
"""End-to-end validation script: runs every fixture through the service with
a real (temporary) SQLite provenance store, checks the fixture expectations,
replays each run, and prints a report. Exit code 0 iff all checks pass.

Usage: python3 scripts/validate.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from njtree import BuildParams, ProvenanceStore, TreeService  # noqa: E402
from njtree.models import NegativeBranchMode  # noqa: E402
from tests.independent import (  # noqa: E402
    leaf_path_lengths,
    parse_newick,
    splits,
    total_residual,
)

FIXTURES = ROOT / "fixtures"
TOL = 1e-9

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(f"{label}: {detail}")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="njtree-validate-")
    service = TreeService(ProvenanceStore(str(Path(tmpdir) / "runs.sqlite")))
    print(f"provenance store: {tmpdir}/runs.sqlite\n")

    # -- additive4: exact recovery -----------------------------------------
    print("fixture additive4 (hand-computed)")
    fx = load("additive4.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    check("exact newick", result.newick == fx["expected"]["newick"], result.newick)
    check("leaf map", result.leaf_map == fx["expected"]["leaf_map"])
    check("zero residual", abs(result.residuals.total_absolute) < TOL)
    paths = leaf_path_lengths(parse_newick(result.newick))
    check("independent path distances",
          abs(total_residual(fx["labels"], fx["matrix"], paths)) < TOL)
    check("first-step tie recorded",
          result.steps[0].tie_count == fx["expected"]["first_step"]["tie_count"]
          and result.steps[0].chosen_labels == fx["expected"]["first_step"]["chosen_labels"])
    check("replay", service.replay(result.run_id).match)

    # -- additive6: topology + zero residual --------------------------------
    print("fixture additive6 (BFS path sums from hand-defined tree)")
    fx = load("additive6.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    check("zero residual", abs(result.residuals.total_absolute) < TOL)
    check("topology (splits)",
          splits(parse_newick(result.newick))
          == splits(parse_newick(fx["expected"]["true_tree_newick"])))
    check("replay", service.replay(result.run_id).match)

    # -- noisy6: residual reported, topology kept ----------------------------
    print("fixture noisy6 (deterministic noise)")
    fx = load("noisy6.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    check("residual reported > 0", result.residuals.total_absolute > 0)
    independent_total = total_residual(
        fx["labels"], fx["matrix"], leaf_path_lengths(parse_newick(result.newick)))
    check("residual matches independent check",
          abs(result.residuals.total_absolute - independent_total) < TOL,
          f"reported={result.residuals.total_absolute} independent={independent_total}")
    check("topology preserved",
          splits(parse_newick(result.newick))
          == splits(parse_newick(fx["expected"]["true_tree_newick"])))
    check("replay", service.replay(result.run_id).match)

    # -- duplicates ----------------------------------------------------------
    print("fixture duplicates (identical rows)")
    fx = load("duplicates.json")
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    check("zero residual", abs(result.residuals.total_absolute) < TOL)
    check("replay", service.replay(result.run_id).match)

    # -- negative_branch: all three declared modes ---------------------------
    print("fixture negative_branch (non-additive, negative limbs)")
    fx = load("negative_branch.json")
    report = service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.REPORT))
    check("report mode newick", report.newick == fx["expected"]["newick_report"], report.newick)
    check("report mode events", len(report.negative_events) == 2)
    check("report mode residual",
          abs(report.residuals.total_absolute - fx["expected"]["total_absolute_residual_report"]) < TOL)
    clamp = service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.CLAMP))
    check("clamp mode newick", clamp.newick == fx["expected"]["newick_clamp"], clamp.newick)
    check("clamp residual exceeds report residual (clamping stays visible)",
          clamp.residuals.total_absolute > report.residuals.total_absolute)
    try:
        service.build_from_matrix(
            fx["labels"], fx["matrix"],
            BuildParams(negative_branch_mode=NegativeBranchMode.ERROR))
        check("error mode aborts", False, "no exception raised")
    except Exception as exc:
        check("error mode aborts", type(exc).__name__ == "ComputationError")

    # -- sequences -----------------------------------------------------------
    print("fixture sequences.fasta (synthetic alignment)")
    fasta_result = service.build_from_fasta((FIXTURES / "sequences.fasta").read_text(), BuildParams())
    check("fasta -> additive4 newick", fasta_result.newick == load("additive4.json")["expected"]["newick"])

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
