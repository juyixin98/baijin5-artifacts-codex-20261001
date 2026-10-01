"""Reproduction experiments for the local-linear RD backend.

Runs the required validation matrix as Monte Carlo experiments:

  1. known jump        - bias / RMSE / coverage of the jump estimator
  2. no jump           - size (false-positive rate) of the jump test
  3. density jump     - level estimate stays unbiased while McCrary flags
  4. sparse boundary   - hard gap is classified NON_IDENTIFIABLE

Across several bandwidths, comparing each fitted jump against the independent
test oracle (``tests/reference.py``). Every experiment is identified by a
correlatable run id, logs progress and versions, and writes both a machine
readable JSON report and a human readable text summary. Failures and
non-executed items are recorded as such rather than hidden.

Usage:
    python -m scripts.run_experiments --reps 200 --out reports
    python -m scripts.run_experiments --quick
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

import scipy
from app.core.estimator import rd_estimate
from app.core.contracts import FailureCategory, RunStatus
from app.dgp import (
    density_discontinuity,
    heaped_discrete,
    no_jump,
    sharp_jump,
    sparse_boundary,
)
from app.logging_setup import configure_logging, log_event
from tests.reference import rd_reference

LOGGER = configure_logging()

BANDWIDTHS = (0.10, 0.20, 0.40)


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    reps: int
    bandwidths: tuple[float, ...]
    seed_base: int


def _versions() -> Dict[str, str]:
    from app import __version__
    return {
        "app": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def _coverage(res, true_tau: float) -> bool | None:
    if res.ci is None:
        return None
    return bool(res.ci[0] <= true_tau <= res.ci[1])


def run_estimation_experiment(
    name: str, dgp_fn, true_tau: float, cfg: ExperimentConfig
) -> Dict[str, Any]:
    """Bias/RMSE/coverage/reference comparison across bandwidths."""
    run_id = f"exp-{name}-{uuid.uuid4().hex[:8]}"
    log_event(LOGGER, logging.INFO, f"experiment {name} start",
              experiment=name, run_id=run_id, reps=cfg.reps,
              bandwidths=list(cfg.bandwidths), true_tau=true_tau,
              step="start", versions=_versions())
    out: Dict[str, Any] = {"experiment": name, "run_id": run_id,
                          "true_tau": true_tau, "by_bandwidth": []}
    for bi, h in enumerate(cfg.bandwidths):
        t0 = time.perf_counter()
        taus, refs, errors, covered = [], [], [], []
        statuses: Dict[str, int] = {}
        failures: Dict[str, int] = {}
        eff_n_l, eff_n_r = [], []
        ref_agreement = []
        for r in range(cfg.reps):
            d = dgp_fn(seed=cfg.seed_base + 10_000 * bi + r)
            res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=h,
                              run_id=f"{run_id}-h{h}-r{r}")
            statuses[res.status.value] = statuses.get(res.status.value, 0) + 1
            if res.status is RunStatus.FAILED:
                cat = res.failure_category.value if res.failure_category else "unknown"
                failures[cat] = failures.get(cat, 0) + 1
                continue
            taus.append(res.tau)
            errors.append(res.tau - true_tau)
            covered.append(_coverage(res, true_tau))
            eff_n_l.append(res.left.n)
            eff_n_r.append(res.right.n)
            ref = rd_reference(d.x, d.y, cutoff=d.cutoff, h=h)
            refs.append(ref.tau)
            ref_agreement.append(abs(res.tau - ref.tau))
        arr = np.asarray(errors, dtype=float)
        cov_vals = [c for c in covered if c is not None]
        row: Dict[str, Any] = {
            "bandwidth": h,
            "successful_reps": len(taus),
            "status_counts": statuses,
            "failure_categories": failures,
            "bias": float(arr.mean()) if arr.size else None,
            "rmse": float(np.sqrt((arr**2).mean())) if arr.size else None,
            "coverage95": float(np.mean(cov_vals)) if cov_vals else None,
            "mean_estimate": float(np.mean(taus)) if taus else None,
            "reference_mean_estimate": float(np.mean(refs)) if refs else None,
            "max_abs_gap_to_reference": float(np.max(ref_agreement))
                if ref_agreement else None,
            "mean_effective_n_left": float(np.mean(eff_n_l)) if eff_n_l else None,
            "mean_effective_n_right": float(np.mean(eff_n_r)) if eff_n_r else None,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
        out["by_bandwidth"].append(row)
        log_event(LOGGER, logging.INFO, f"experiment {name} bandwidth {h} done",
                  experiment=name, run_id=run_id, bandwidth=h,
                  bias=row["bias"], rmse=row["rmse"],
                  coverage=row["coverage95"], failures=failures,
                  step="bandwidth_complete")
    return out


def run_size_experiment(cfg: ExperimentConfig) -> Dict[str, Any]:
    """False-positive (reject) rate under a true jump of zero."""
    run_id = f"exp-size-{uuid.uuid4().hex[:8]}"
    out: Dict[str, Any] = {"experiment": "no_jump_test_size", "run_id": run_id,
                          "by_bandwidth": []}
    for h in cfg.bandwidths:
        rejects = 0
        total = 0
        for r in range(cfg.reps):
            d = no_jump(n=2000, seed=cfg.seed_base + r)
            res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=h,
                              run_id=f"{run_id}-h{h}-r{r}")
            if res.status is RunStatus.FAILED or res.pvalue is None:
                continue
            total += 1
            rejects += int(res.pvalue < 0.05)
        out["by_bandwidth"].append({
            "bandwidth": h, "evaluable_reps": total,
            "reject_rate_at_0.05": rejects / total if total else None,
        })
    return out


def run_sparse_experiment(cfg: ExperimentConfig) -> Dict[str, Any]:
    """Hard-gap classification across bandwidths.

    With a fixed empty gap the boundary is nonparametrically unidentified
    while the gap is at least as large as the local data span
    (extrapolation factor >= 1). A wide enough window whose span exceeds the
    gap can estimate the boundary *by linear extrapolation*; that is returned
    with a warning, not silently treated as ordinary identification. We
    record both the category and the extrapolation factor to make the
    trade-off explicit.
    """
    run_id = f"exp-sparse-{uuid.uuid4().hex[:8]}"
    rows = []
    for h in (0.05, 0.10, 0.20, 0.40, 0.80):
        cats: Dict[str, int] = {}
        factors: List[float] = []
        flagged_extrapolation = 0
        for r in range(cfg.reps):
            d = sparse_boundary(n=800, inner_gap=0.25, seed=cfg.seed_base + r)
            res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=h,
                              run_id=f"{run_id}-h{h}-r{r}")
            key = (res.failure_category.value if res.failure_category
                   else f"status:{res.status.value}")
            cats[key] = cats.get(key, 0) + 1
            fac = res.diagnostics.get("extrapolation_factor")
            if fac:
                factors.append(max(float(fac["left"]), float(fac["right"])))
            if any("extrapolates across a gap" in w for w in res.warnings):
                flagged_extrapolation += 1
        rows.append({
            "bandwidth": h,
            "category_counts": cats,
            "max_extrapolation_factor": float(np.max(factors)) if factors else None,
            "runs_flagged_for_extrapolation": flagged_extrapolation,
        })
    return {"experiment": "sparse_boundary_classification", "run_id": run_id,
            "expected_category_while_gap_dominates":
                FailureCategory.NON_IDENTIFIABLE.value,
            "by_bandwidth": rows}


def run_density_experiment(cfg: ExperimentConfig) -> Dict[str, Any]:
    """Density jump: level unbiased; McCrary rejection rate high."""
    run_id = f"exp-density-{uuid.uuid4().hex[:8]}"
    level_err, mccrary_reject, theta = [], 0, []
    for r in range(cfg.reps):
        d = density_discontinuity(n=2000, right_share=0.75,
                                  seed=cfg.seed_base + r)
        res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=0.25,
                          run_id=f"{run_id}-r{r}")
        if res.status is not RunStatus.FAILED:
            level_err.append(res.tau - 0.0)
        mcc = res.diagnostics.get("mccrary")
        if mcc and mcc["passed"] is False:
            mccrary_reject += 1
        if mcc and mcc["details"].get("theta_log_density_jump") is not None:
            theta.append(mcc["details"]["theta_log_density_jump"])
    return {
        "experiment": "density_discontinuity", "run_id": run_id,
        "reps": cfg.reps,
        "level_bias": float(np.mean(level_err)) if level_err else None,
        "level_rmse": float(np.sqrt(np.mean(np.asarray(level_err) ** 2)))
            if level_err else None,
        "mccrary_reject_rate": mccrary_reject / cfg.reps,
        "mean_theta_log_density_jump": float(np.mean(theta)) if theta else None,
        "true_theta": float(np.log(3.0)),
    }


def format_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("LOCAL-LINEAR RD BACKEND - REPRODUCTION REPORT")
    lines.append("=" * 78)
    lines.append(f"generated_run : {report['generated_run_id']}")
    lines.append(f"reps/bandwidth: {report['config']['reps']}")
    lines.append(f"versions      : {json.dumps(report['versions'])}")
    lines.append("")
    for exp in report["experiments"]:
        lines.append("-" * 78)
        lines.append(f"EXPERIMENT: {exp['experiment']}  (run_id={exp['run_id']})")
        if "by_bandwidth" in exp:
            for row in exp["by_bandwidth"]:
                h = row.get("bandwidth")
                if "bias" in row:
                    lines.append(
                        f"  h={h:<5} n_ok={row['successful_reps']:<4} "
                        f"bias={row['bias']} rmse={row['rmse']} "
                        f"cov95={row['coverage95']} "
                        f"ref_gap_max={row['max_abs_gap_to_reference']:.2e} "
                        f"effN~({row['mean_effective_n_left']:.0f},"
                        f"{row['mean_effective_n_right']:.0f}) "
                        f"fail={row['failure_categories']}")
                elif "reject_rate_at_0.05" in row:
                    lines.append(
                        f"  h={h:<5} evaluable={row['evaluable_reps']:<4} "
                        f"reject_rate@5%={row['reject_rate_at_0.05']}")
                elif "category_counts" in row:
                    lines.append(
                        f"  h={h:<5} classification={row['category_counts']} "
                        f"max_extrap_factor={row['max_extrapolation_factor']} "
                        f"flagged={row['runs_flagged_for_extrapolation']}")
        for k in ("level_bias", "level_rmse", "mccrary_reject_rate",
                  "mean_theta_log_density_jump", "true_theta"):
            if k in exp:
                lines.append(f"  {k} = {exp[k]}")
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=200)
    parser.add_argument("--out", type=str, default="reports")
    parser.add_argument("--quick", action="store_true",
                        help="small rep count for a fast smoke run")
    args = parser.parse_args(argv)
    reps = 20 if args.quick else args.reps
    cfg = ExperimentConfig("matrix", reps, BANDWIDTHS, seed_base=1000)

    experiments = [
        run_estimation_experiment(
            "known_jump", lambda seed: sharp_jump(n=2000, tau=10.0, seed=seed),
            10.0, cfg),
        run_estimation_experiment(
            "no_jump", lambda seed: no_jump(n=2000, seed=seed), 0.0, cfg),
        run_size_experiment(cfg),
        run_density_experiment(cfg),
        run_sparse_experiment(cfg),
    ]
    report = {
        "generated_run_id": f"report-{uuid.uuid4().hex[:10]}",
        "config": {"reps": reps, "bandwidths": list(BANDWIDTHS)},
        "versions": _versions(),
        "experiments": experiments,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "rd_experiments.json"
    txt_path = out_dir / "rd_experiments.txt"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    txt_path.write_text(format_report(report))
    print(format_report(report))
    print(f"wrote {json_path} and {txt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
