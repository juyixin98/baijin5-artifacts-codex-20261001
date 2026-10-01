"""Command-line demo: run the full CUPED/Lin pipeline on a sample dataset.

Usage:
    python scripts/run_demo.py data/sample/balanced.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.contracts import Settings  # noqa: E402
from app.core.service import run_analysis  # noqa: E402


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else ROOT / "data" / "sample" / "balanced.json"
    truth_path = path.with_suffix(".truth.json")
    payload = json.loads(path.read_text())
    truth = json.loads(truth_path.read_text()) if truth_path.exists() else None

    result = run_analysis(payload, Settings())

    print(f"run_id={result.run_id}  fingerprint={result.data_fingerprint}")
    print(f"experiment={result.experiment_name}  n={result.n_rows} "
          f"(complete={result.n_complete_rows}, dropped rows={result.n_dropped_rows})")
    print(f"versions={result.versions}")
    print(f"covariates requested={result.requested_covariates}")
    print(f"covariates used={result.used_covariates}")
    print(f"covariates dropped={result.dropped_covariates}")
    print()
    print(f"{'estimator':<20}{'estimate':>12}{'SE':>12}{'CI95 low':>12}{'CI95 high':>12}")
    for label, est in (("unadjusted", result.unadjusted),
                       ("CUPED", result.cuped),
                       ("Lin/ANCOVA", result.lin)):
        print(f"{label:<20}{est.estimate:>12.4f}{est.se:>12.4f}"
              f"{est.ci_low:>12.4f}{est.ci_high:>12.4f}")
    print()
    theta_dict = {name: round(float(v), 4)
                  for name, v in zip(result.cuped.covariate_names,
                                     result.cuped.theta)}
    print(f"CUPED theta={theta_dict}")
    print(f"theta source={result.theta_source}  SE family={result.se_type}")
    print()
    print("cross-checks:")
    for check in result.cross_checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
        for key, value in check.values.items():
            print(f"        {key} = {value:.6f}" if isinstance(value, float)
                  else f"        {key} = {value}")
    print()
    print("diagnostics:")
    for d in result.diagnostics:
        status = "INCLUDED" if d.included else f"EXCLUDED({d.reason_excluded})"
        flag = " LEAKAGE!" if d.leakage_flag else ""
        print(f"  {d.name:<12} {status:<22} SMD={d.smd:+.3f}{flag}")
    if result.warnings:
        print("\nwarnings:")
        for w in result.warnings:
            print(f"  - {w}")
    if truth:
        print(f"\nground truth tau = {truth['tau']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
