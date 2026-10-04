"""End-to-end verification script.

Runs every fixture through the real pipeline (parse -> validate -> NJ ->
residuals -> Newick -> SQLite provenance) and then checks the results
AGAINST HAND-DERIVED reference values and against an independent Newick
parser (scripts/independent_newick.py) that shares no code with the
service.

Exit code 0 iff every check passes. Each check prints the run id so the
structured log (nj_service.log) can be consulted to replay the decision.

Usage: python scripts/verify.py [--db PATH] [--log PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import runlog  # noqa: E402
from app.models import TreeOptions, TreeRequest  # noqa: E402
from app.service import build_tree  # noqa: E402
from app.store import RunStore  # noqa: E402
from scripts.independent_newick import leaf_distances, parse_newick  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TOL = 1e-9

failures: list[str] = []


def check(name: str, condition: bool, detail: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}: {detail}")
    if not condition:
        failures.append(f"{name}: {detail}")


def independent_residual_sum(newick: str, labels: list[str], matrix: list[list[float]],
                             leaf_map: dict[str, str]) -> float:
    """Total absolute residual recomputed from the emitted Newick alone."""
    distances = leaf_distances(parse_newick(newick))
    id_by_label = {label: leaf_id for leaf_id, label in leaf_map.items()}
    total = 0.0
    for i, li in enumerate(labels):
        for j in range(i + 1, len(labels)):
            lj = labels[j]
            a, b = id_by_label[li], id_by_label[lj]
            pair = (a, b) if a <= b else (b, a)
            total += abs(matrix[i][j] - distances[pair])
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--log", default=None)
    args = parser.parse_args()

    tmp = tempfile.TemporaryDirectory()
    db_path = args.db or str(Path(tmp.name) / "verify_runs.db")
    runlog.configure_logging(args.log or str(Path(tmp.name) / "verify.log"))
    store = RunStore(db_path)

    # 1. Additive 4-taxon: exact reconstruction, zero residual.
    fx = json.loads((FIXTURES / "additive_4taxon.json").read_text())
    req = TreeRequest(
        input_format="matrix",
        matrix={"labels": fx["labels"], "distances": fx["matrix"]},
    )
    print("fixture: additive_4taxon")
    resp = build_tree(req, store)
    print(f"  run_id={resp.run_id} newick={resp.newick}")
    check("additive_4taxon", resp.newick == fx["expected"]["newick"],
          f"newick equals hand-derived reference {fx['expected']['newick']}")
    check("additive_4taxon", resp.residuals.sum_abs < TOL,
          f"residual sum_abs={resp.residuals.sum_abs:.3g} is zero")
    check("additive_4taxon", resp.leaf_map == fx["expected"]["leaf_map"],
          "leaf identity map matches")
    run_fixture_independent = independent_residual_sum(
        resp.newick, fx["labels"], fx["matrix"], resp.leaf_map
    )
    check("additive_4taxon", run_fixture_independent < TOL,
          "independent Newick parse reproduces zero residual")

    # 2. Negative branch, all three declared modes.
    fx = json.loads((FIXTURES / "negative_branch_4taxon.json").read_text())
    print("fixture: negative_branch_4taxon (allow)")
    resp = build_tree(
        TreeRequest(
            input_format="matrix",
            matrix={"labels": fx["labels"], "distances": fx["matrix"]},
            options=TreeOptions(negative_branch_mode="allow"),
        ),
        store,
    )
    print(f"  run_id={resp.run_id} newick={resp.newick}")
    check("negative_allow", resp.newick == fx["expected"]["allow"]["newick"],
          "negative branch kept and visible in Newick")
    check("negative_allow", resp.residuals.sum_abs < TOL,
          "matrix is additive with a negative edge: residual stays zero")
    check("negative_allow",
          any(e.type == "negative_branch" for e in resp.events),
          "negative_branch event recorded")

    print("fixture: negative_branch_4taxon (clamp)")
    resp = build_tree(
        TreeRequest(
            input_format="matrix",
            matrix={"labels": fx["labels"], "distances": fx["matrix"]},
            options=TreeOptions(negative_branch_mode="clamp"),
        ),
        store,
    )
    print(f"  run_id={resp.run_id} newick={resp.newick}")
    check("negative_clamp",
          abs(resp.residuals.sum_abs - fx["expected"]["clamp"]["residual_sum_abs"]) < TOL,
          f"clamping distorts the fit: sum_abs={resp.residuals.sum_abs}")
    check("negative_clamp",
          any(e.type == "branch_clamped" for e in resp.events),
          "branch_clamped event recorded (not silent)")

    # 3. Noisy 5-taxon: positive residual, deterministic output.
    fx = json.loads((FIXTURES / "noisy_5taxon.json").read_text())
    req = TreeRequest(
        input_format="matrix",
        matrix={"labels": fx["labels"], "distances": fx["matrix"]},
    )
    print("fixture: noisy_5taxon")
    resp1 = build_tree(req, store)
    resp2 = build_tree(req, store)
    print(f"  run_ids={resp1.run_id},{resp2.run_id} newick={resp1.newick}")
    check("noisy_5taxon", resp1.residuals.sum_abs > 0,
          f"non-additive input reports positive residual "
          f"({resp1.residuals.sum_abs:.6g})")
    check("noisy_5taxon", resp1.newick == resp2.newick,
          "identical input gives byte-identical Newick (stable ties)")
    indep = independent_residual_sum(resp1.newick, fx["labels"], fx["matrix"],
                                      resp1.leaf_map)
    check("noisy_5taxon", abs(indep - resp1.residuals.sum_abs) < TOL,
          "independent residual check matches the report")

    # 4. Duplicate leaves.
    fx = json.loads((FIXTURES / "duplicate_leaves.json").read_text())
    print("fixture: duplicate_leaves")
    resp = build_tree(
        TreeRequest(input_format="fasta", sequences=fx["fasta"]), store
    )
    print(f"  run_id={resp.run_id} newick={resp.newick}")
    check("duplicate_leaves", resp.newick == fx["expected"]["newick"],
          "identical sequences form a zero-length cherry")
    check("duplicate_leaves", resp.leaf_map == fx["expected"]["leaf_map"],
          "leaf identity map preserves both duplicate labels")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all verification checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
