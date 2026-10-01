#!/usr/bin/env python3
"""Run the local reproducibility experiments and emit JSONL + summary files.

Examples:
    python3 scripts/run_experiments.py --quick        # 50 reps, smoke run
    python3 scripts/run_experiments.py                # 500 reps, frozen seeds
    python3 scripts/run_experiments.py --reps 2000 --tests 1000
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.runner import mixed_experiment, null_experiment, run_experiment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=500)
    parser.add_argument("--tests", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--quick", action="store_true",
                        help="50 reps / 200 tests smoke run")
    parser.add_argument("--out", type=str,
                        default=str(ROOT / "artifacts"))
    args = parser.parse_args()
    reps, tests = (50, 200) if args.quick else (args.reps, args.tests)

    configs = [
        null_experiment(reps, tests, args.seed),
        mixed_experiment(reps, tests, args.seed, nonnull_fraction=0.2,
                         beta_a=0.05),
    ]
    overall = {}
    for cfg in configs:
        result = run_experiment(cfg, args.out)
        overall[cfg.name] = result["summary"]
        print(f"[{cfg.name}] {json.dumps(result['summary'], sort_keys=True)}")
        print(f"  log: {result['log_path']}")
    out_path = pathlib.Path(args.out) / "all_summaries.json"
    out_path.write_text(json.dumps(overall, indent=2, sort_keys=True))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
