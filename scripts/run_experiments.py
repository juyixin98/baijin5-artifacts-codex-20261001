#!/usr/bin/env python3
"""Reproduce all Monte Carlo evidence for the LORD++ teaching service.

Outputs JSON artifacts under artifacts/tests/ with:

* frozen contract parameters,
* every replication's run number, seed, counts and FDP (replayable),
* aggregate FDR / power with standard errors,
* an explicit judgment per experiment with the reason for the verdict.

Usage:
    python scripts/run_experiments.py [--reps N] [--steps M] [--out DIR]

This script is deterministic for fixed flags. It uses the INDEPENDENT
reference implementation in app/simulation.py, not the service kernel, to
produce reference answers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.diagnostics import account, aggregate_fdr  # noqa: E402
from app.simulation import (  # noqa: E402
    DEFAULT_SEED,
    SCHEDULE_HORIZON,
    gaussian_stream,
    null_stream,
    run_experiment,
    run_lordpp_reference,
)
from app.statistics import CONTRACT_VERSION, DEFAULT_ALPHA, DEFAULT_W0_FRACTION  # noqa: E402

# FDR <= alpha is an expectation claim. Finite MC estimates carry sampling
# error; a run is marked CONSISTENT when the upper 95% CI bound stays under the
# loose teaching tolerance alpha + MARGIN, and FLAGs otherwise for inspection.
TEACHING_MARGIN = 0.05


def short_trajectory_artifact() -> dict:
    """The five-step hand trajectory, decisions + rationale per slot."""
    ps = [0.0005, 0.5, 0.0005, 0.9, 0.0004]
    ref = run_lordpp_reference(ps, alpha=0.05, w0=0.045, horizon=1000)
    steps = []
    for t, (p, thr, w, rej) in enumerate(
        zip(ps, ref["thresholds"], ref["wealth"], ref["rejected"]), start=1
    ):
        steps.append(
            {
                "t": t,
                "p_t": p,
                "wealth_W_t": w,
                "threshold_alpha_t": thr,
                "decision": "reject" if rej else "keep",
                "reason": (
                    f"alpha_{t} computed from rejection history at times < {t} only; "
                    + (f"{p} <= {thr:.12g}" if rej else f"{p} > {thr:.12g}")
                ),
            }
        )
    return {
        "name": "hand-trajectory-5-steps",
        "params": {"alpha": 0.05, "w0": 0.045, "b": 0.005, "horizon": 1000},
        "rejection_times": [t for t, r in enumerate(ref["rejected"], 1) if r],
        "steps": steps,
    }


def judgment(name: str, result, requirement: str) -> dict:
    est = result.fdr_estimate
    upper = result.ci95_high
    ok = upper <= DEFAULT_ALPHA + TEACHING_MARGIN
    return {
        "experiment": name,
        "requirement": requirement,
        "fdr_estimate": est,
        "ci95_high": upper,
        "verdict": "CONSISTENT" if ok else "FLAG_FOR_INSPECTION",
        "reason": (
            f"empirical FDR {est:.4f} with 95% upper bound {upper:.4f}; "
            f"teaching tolerance alpha+{TEACHING_MARGIN} = "
            f"{DEFAULT_ALPHA + TEACHING_MARGIN:.2f}. FDR is an expectation over "
            "replications; a single run is never guaranteed below alpha."
        ),
        "marginal_power": result.marginal_power,
        "any_rejection_rate": result.any_rejection_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=300)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--out", type=str, default="artifacts/tests")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")

    payload: dict = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": started,
        "parameters": {
            "alpha": DEFAULT_ALPHA,
            "w0": DEFAULT_ALPHA * DEFAULT_W0_FRACTION,
            "horizon": SCHEDULE_HORIZON,
            "reps": args.reps,
            "steps_per_run": args.steps,
            "base_seed": DEFAULT_SEED,
            "teaching_margin": TEACHING_MARGIN,
        },
        "experiments": [],
        "judgments": [],
    }

    # 1) Global-null streams: every rejection is a false discovery; FDR equals
    #    the probability of at least one false rejection here.
    null_res = run_experiment(
        name="global-null-uniform",
        n_per_run=args.steps,
        n_replications=args.reps,
        pi1=0.0,
        effect=0.0,
        stream_kind="uniform-null",
    )
    payload["experiments"].append(_exp_dict(null_res))
    payload["judgments"].append(
        judgment(
            "global-null-uniform",
            null_res,
            "under the global null FDP is 1{any false rejection}; its mean should"
            " stay at/below alpha (online FWER/FDR control)",
        )
    )

    # 2) Mixed Gaussian stream: 30% fixed-ish alternatives at effect 3.5.
    mixed_res = run_experiment(
        name="mixed-gaussian-pi1-0.30-effect-3.5",
        n_per_run=args.steps,
        n_replications=args.reps,
        pi1=0.30,
        effect=3.5,
        stream_kind="gaussian-two-sided",
    )
    payload["experiments"].append(_exp_dict(mixed_res))
    payload["judgments"].append(
        judgment(
            "mixed-gaussian-pi1-0.30-effect-3.5",
            mixed_res,
            "with many true alternatives, empirical FDR should remain at/below"
            " alpha while marginal power is clearly positive",
        )
    )
    # Separate, stricter statistical check: power positive.
    payload["judgments"][-1]["power_positive"] = mixed_res.marginal_power > 0.05

    # 3) Hand trajectory.
    payload["hand_trajectory"] = short_trajectory_artifact()

    # 4) Cross-check diagnostic accounting on one mixed stream.
    stream = gaussian_stream(args.steps, 0.3, effect=3.5, seed=DEFAULT_SEED)
    ref = run_lordpp_reference(stream.p_values.tolist())
    acct = account(ref["rejected"], stream.is_alternative.tolist())
    payload["sample_stream_account"] = {
        "seed": DEFAULT_SEED,
        **acct.to_dict(),
    }

    out_path = out_dir / "experiment-report.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)

    # Also write a compact line log greppable by run number.
    log_path = out_dir / "replication-log.tsv"
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("experiment\trun_no\tseed\tn\tn_alt\trejections\tfalse_disc\tfdp\n")
        for exp in payload["experiments"]:
            for r in exp["replications"]:
                fh.write(
                    f"{exp['name']}\t{r['run_no']}\t{r['seed']}\t{r['n']}\t"
                    f"{r['n_alternative']}\t{r['rejections']}\t"
                    f"{r['false_discoveries']}\t{r['fdp']:.6f}\n"
                )

    print(f"wrote {out_path}")
    print(f"wrote {log_path}")
    for j in payload["judgments"]:
        print(
            f"[{j['verdict']}] {j['experiment']}: "
            f"FDR={j['fdr_estimate']:.4f} CI95hi={j['ci95_high']:.4f} "
            f"power={j['marginal_power']:.3f}"
        )
    print("hand trajectory rejection times:", payload["hand_trajectory"]["rejection_times"])
    return 0


def _exp_dict(result) -> dict:
    d = result.to_dict()
    d["replications"] = [r.__dict__ for r in result.replications]
    return d


if __name__ == "__main__":
    raise SystemExit(main())
