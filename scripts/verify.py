#!/usr/bin/env python3
"""End-to-end verification script.

Runs every synthetic fixture through the autodiff core, verifies analytic
gradients against independent central finite differences, exercises the
in-place version guard, and prints a machine-readable summary.  Exits non-zero
if any check is REJECTED; UNABLE results also fail the run (they represent
checks that could not be decided, never silently treated as pass).

Usage:
    python scripts/verify.py                 # all scenarios
    python scripts/verify.py matmul_mlp      # one scenario
    python scripts/verify.py --json          # JSON report on stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running directly from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autodiff.api.service import run_gradcheck, run_inplace_check  # noqa: E402
from autodiff.diagnostics import ACCEPTED, REJECTED, UNABLE  # noqa: E402
from autodiff.fixtures import SCENARIOS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*",
                        help="scenario ids (default: all)")
    parser.add_argument("--json", action="store_true",
                        help="emit a single JSON report")
    parser.add_argument("--eps", type=float, default=None)
    parser.add_argument("--atol", type=float, default=None)
    parser.add_argument("--rtol", type=float, default=None)
    args = parser.parse_args(argv)

    names = args.scenarios or sorted(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {unknown}", file=sys.stderr)
        return 2

    reports = []
    for name in names:
        reports.append(run_gradcheck(
            name, eps=args.eps, atol=args.atol, rtol=args.rtol,
        ))
    inplace_mutated = run_inplace_check(mutate=True)
    inplace_clean = run_inplace_check(mutate=False)

    if args.json:
        payload = {
            "gradient_checks": reports,
            "inplace_check": {
                "mutated": inplace_mutated,
                "clean": inplace_clean,
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        for r in reports:
            _print_grad_report(r)
        _print_inplace("in-place mutation", inplace_mutated, expect_rejected=True)
        _print_inplace("clean graph", inplace_clean, expect_rejected=False)

    grad_bad = [r["scenario"] for r in reports if r["status"] != ACCEPTED]
    inplace_ok = (
        inplace_mutated["accepted"] is False
        and inplace_mutated["failure_category"] == "stale_graph_version_mismatch"
        and inplace_clean["accepted"] is True
    )
    if grad_bad or not inplace_ok:
        print("RESULT: FAIL", file=sys.stderr)
        if grad_bad:
            print(f"  gradient checks not accepted: {grad_bad}", file=sys.stderr)
        if not inplace_ok:
            print("  in-place guard behaviour incorrect", file=sys.stderr)
        return 1
    print("RESULT: PASS - all gradient checks accepted and version guard verified")
    return 0


def _print_grad_report(r: dict) -> None:
    print(f"[{r['status']}] scenario={r['scenario']} "
          f"eps={r['eps']:g} atol={r['atol']:g} rtol={r['rtol']:g}")
    for leaf in r["leaves"]:
        cat = f" category={leaf['category']}" if leaf["category"] else ""
        print(f"    - {leaf['name']}: {leaf['status']}{cat} "
              f"max_abs={leaf['max_abs_err']:.3e} max_rel={leaf['max_rel_err']:.3e} "
              f"shape={tuple(leaf['shape'])}")


def _print_inplace(label: str, r: dict, *, expect_rejected: bool) -> None:
    state = "correctly rejected" if expect_rejected else "accepted"
    print(f"[in-place] {label}: accepted={r['accepted']} "
          f"category={r['failure_category']} ({state})")


if __name__ == "__main__":
    raise SystemExit(main())
