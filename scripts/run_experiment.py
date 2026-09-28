#!/usr/bin/env python3
"""Run the replication experiment and persist results + replayable run DB.

Usage:
    python scripts/run_experiment.py [configs/experiment.json] \\
        [artifacts/experiment_results.json] [artifacts/runs.db]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aipw_backend.experiment import run_experiment  # noqa: E402


def main(argv: list[str]) -> int:
    config_path = Path(argv[1] if len(argv) > 1 else "configs/experiment.json")
    out_path = Path(
        argv[2] if len(argv) > 2 else "artifacts/experiment_results.json"
    )
    db_path = Path(argv[3] if len(argv) > 3 else "artifacts/runs.db")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    results = run_experiment(config, db_path)
    out_path.write_text(
        json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"wrote {out_path} and replay database {db_path}")
    for name, s in results["iid_scenarios"].items():
        agg = s["summary"]
        print(
            f"  {name:16s} bias={agg['bias']:+.4f} "
            f"coverage={agg['coverage_95']:.3f} "
            f"se_ratio={agg['se_ratio_analytic_over_empirical']:.3f}"
        )
    ce = results["cluster_experiment"]
    print(
        "  cluster-aware   coverage="
        f"{ce['cluster_units']['coverage_95']:.3f}"
    )
    print(
        "  rows-as-iid     coverage="
        f"{ce['iid_rows_as_independent']['coverage_95']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
