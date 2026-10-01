"""Replication experiments.

Runs the validation plan required by the statistical contract and writes a
machine-readable JSON report plus a human-readable Markdown summary:

1. **Point-estimator validity** - across Monte Carlo replications of the
   known-jump and no-jump DGPs, record bias, RMSE, Monte-Carlo SE, mean
   analytic HC3 SE, 95% CI coverage and null rejection rate.
2. **Bandwidth sweep** - estimate the same DGP at several bandwidths and
   report tau, effective N and the identification window.
3. **Reference agreement** - core vs the independent explicit-sums path,
   the numerical-optimisation path, and the bootstrap-t.
4. **Diagnostic power / honest failure** - density-sorting flag rate, and
   the fraction of sparse-boundary datasets marked unidentified.

Everything is seeded and the report records versions, seeds and the
parameters of every scenario so a run is reproducible. Progress is logged
through the shared run-identity logger.

Usage::

    python -m experiments.replication --reps 200 --out results
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app import logging_setup
from app.config import Settings
from app.contract import (
    BandwidthMethod,
    DataPoint,
    InferenceMethod,
    RDRequest,
    RunStatus,
)
from app.datasets import (
    density_sorting,
    no_jump,
    sharp_jump,
    sparse_boundary,
)
from app.pipeline import run_analysis
from app import reference as ref

SETTINGS = Settings(
    db_path=":memory:",
    min_obs_per_side=10,
    bootstrap_reps=0,
    default_alpha=0.05,
    log_level="INFO",
)
FIXED_BANDWIDTH = 0.2
MC_SAMPLE_N = 1000


def _request(x: np.ndarray, y: np.ndarray, label: str, seed: int) -> RDRequest:
    return RDRequest(
        input_label=label,
        data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(x, y)],
        cutoff=0.0,
        bandwidth_method=BandwidthMethod.MANUAL,
        bandwidth=FIXED_BANDWIDTH,
        inference=InferenceMethod.HC3,
        bootstrap_reps=0,
        run_id=f"mc-{label}-{seed}",
    )


def monte_carlo(name: str, factory, reps: int, *, null: bool) -> dict:
    logging_setup.step("monte_carlo", f"starting {name}", reps=reps)
    taus: list[float] = []
    ses: list[float] = []
    covered: list[int] = []
    rejected: list[int] = []
    unidentified = 0
    t0 = time.time()
    for r in range(reps):
        dgp = factory(seed=10_000 + r)
        resp = run_analysis(_request(dgp.x, dgp.y, name, 10_000 + r), SETTINGS)
        if resp.status is not RunStatus.OK:
            unidentified += 1
            continue
        assert resp.estimate is not None and resp.estimate.se is not None
        taus.append(resp.estimate.tau)
        ses.append(resp.estimate.se)
        covered.append(int(resp.estimate.ci_low <= dgp.tau <= resp.estimate.ci_high))
        rejected.append(int(resp.estimate.p_value < 0.05))
        if (r + 1) % 25 == 0:
            logging_setup.step(
                "monte_carlo", f"{name} progress", done=r + 1, reps=reps
            )
    tau_arr = np.asarray(taus)
    se_arr = np.asarray(ses)
    truth = 0.0 if null else 3.0
    result = {
        "scenario": name,
        "reps_requested": reps,
        "reps_estimated": int(tau_arr.size),
        "unidentified": unidentified,
        "true_tau": truth,
        "bias": float(np.mean(tau_arr - truth)),
        "rmse": float(np.sqrt(np.mean((tau_arr - truth) ** 2))),
        "mc_sd": float(np.std(tau_arr, ddof=1)),
        "mean_analytic_se": float(np.mean(se_arr)),
        "se_ratio": float(np.mean(se_arr) / np.std(tau_arr, ddof=1)),
        "coverage_95": float(np.mean(covered)),
        "rejection_rate_05": float(np.mean(rejected)),
        "elapsed_s": round(time.time() - t0, 2),
    }
    logging_setup.step("monte_carlo", f"finished {name}", **result)
    return result


def bandwidth_sweep(reps_seed: int = 4242) -> dict:
    dgp = sharp_jump(n=4000, seed=reps_seed)
    rows = []
    for mult in (0.5, 0.75, 1.0, 1.5, 2.0):
        h = FIXED_BANDWIDTH * mult
        req = RDRequest(
            input_label=f"bw-sweep-{mult}",
            data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(dgp.x, dgp.y)],
            cutoff=0.0,
            bandwidth_method=BandwidthMethod.MANUAL,
            bandwidth=h,
            inference=InferenceMethod.HC3,
            bootstrap_reps=0,
        )
        resp = run_analysis(req, SETTINGS)
        assert resp.status is RunStatus.OK and resp.estimate is not None
        rows.append(
            {
                "bandwidth": h,
                "multiplier": mult,
                "tau": resp.estimate.tau,
                "se": resp.estimate.se,
                "n_left": resp.left_fit.n_in_window,
                "n_right": resp.right_fit.n_in_window,
                "eff_n_left": resp.left_fit.effective_n,
                "eff_n_right": resp.right_fit.effective_n,
                "range_left": resp.left_fit.x_at_cutoff_probe,
                "range_right": resp.right_fit.x_at_cutoff_probe,
            }
        )
    return {"true_tau": dgp.tau, "rows": rows}


def reference_agreement() -> dict:
    dgp = sharp_jump(n=4000, seed=555)
    x, y = dgp.x, dgp.y
    h = FIXED_BANDWIDTH
    req = RDRequest(
        input_label="reference-agreement",
        data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(x, y)],
        bandwidth_method=BandwidthMethod.MANUAL,
        bandwidth=h,
        inference=InferenceMethod.HC3,
        bootstrap_reps=0,
    )
    resp = run_analysis(req, SETTINGS)
    core_tau = resp.estimate.tau
    explicit = ref.explicit_jump(x, y, 0.0, h, "triangular")
    optimized = (
        ref.optimized_side_intercept(x, y, 0.0, h, "triangular", "right")
        - ref.optimized_side_intercept(x, y, 0.0, h, "triangular", "left")
    )
    boott = ref.wild_bootstrap_t(x, y, 0.0, h, "triangular", reps=999, seed=9)
    return {
        "true_tau": dgp.tau,
        "core": core_tau,
        "explicit_sums": explicit,
        "numeric_optimization": optimized,
        "abs_diff_explicit": abs(core_tau - explicit),
        "abs_diff_optimized": abs(core_tau - optimized),
        "bootstrap_t": boott,
    }


def diagnostic_rates(reps: int) -> dict:
    density_flagged = 0
    density_runs = 0
    sparse_unidentified = 0
    for r in range(reps):
        d = density_sorting(seed=20_000 + r)
        resp = run_analysis(
            _request(d.x, d.y, "density", 20_000 + r), SETTINGS
        )
        density_diag = next(
            z for z in resp.diagnostics if z.code.value == "density_discontinuity"
        )
        density_runs += 1
        if density_diag.severity.value == "warning":
            density_flagged += 1

        s = sparse_boundary(seed=30_000 + r)
        resp_s = run_analysis(
            _request(s.x, s.y, "sparse", 30_000 + r), SETTINGS
        )
        if resp_s.status is RunStatus.UNIDENTIFIED:
            sparse_unidentified += 1
    return {
        "density_sorting_flag_rate": density_flagged / density_runs,
        "density_false_outcome_jump_check": "see monte_carlo no_jump coverage",
        "sparse_unidentified_rate": sparse_unidentified / reps,
        "reps": reps,
    }


def build_report(reps: int) -> dict:
    logging_setup.bind_context("replication", "experiments/replication.py", SETTINGS)
    versions = SETTINGS.versions()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "versions": versions,
        "parameters": {
            "monte_carlo_reps": reps,
            "sample_n": MC_SAMPLE_N,
            "fixed_bandwidth": FIXED_BANDWIDTH,
            "alpha": 0.05,
        },
    }
    report["monte_carlo"] = [
        monte_carlo(
            "sharp_jump",
            lambda seed: sharp_jump(n=MC_SAMPLE_N, seed=seed),
            reps,
            null=False,
        ),
        monte_carlo(
            "no_jump",
            lambda seed: no_jump(n=MC_SAMPLE_N, seed=seed),
            reps,
            null=True,
        ),
    ]
    report["bandwidth_sweep"] = bandwidth_sweep()
    report["reference_agreement"] = reference_agreement()
    report["diagnostic_rates"] = diagnostic_rates(max(40, reps // 4))
    return report


def to_markdown(report: dict) -> str:
    lines = [
        "# RD replication report",
        "",
        f"Generated: {report['generated_at']}",
        f"Versions: {json.dumps(report['versions'])}",
        "",
        "## Monte Carlo",
        "",
        "| scenario | reps | bias | RMSE | MC SD | mean SE | SE/SD | coverage 95% | reject @5% | unidentified |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["monte_carlo"]:
        lines.append(
            f"| {row['scenario']} | {row['reps_estimated']} | {row['bias']:.4f} | "
            f"{row['rmse']:.4f} | {row['mc_sd']:.4f} | {row['mean_analytic_se']:.4f} | "
            f"{row['se_ratio']:.3f} | {row['coverage_95']:.3f} | "
            f"{row['rejection_rate_05']:.3f} | {row['unidentified']} |"
        )
    lines += [
        "",
        "## Bandwidth sweep (sharp_jump, n=4000, true tau=3)",
        "",
        "| h | tau | SE | eff N (L/R) | window span (L/R) |",
        "|---:|---:|---:|---:|---:|",
    ]
    for r in report["bandwidth_sweep"]["rows"]:
        lines.append(
            f"| {r['bandwidth']:.3f} | {r['tau']:.4f} | {r['se']:.4f} | "
            f"{r['eff_n_left']:.1f}/{r['eff_n_right']:.1f} | "
            f"{r['range_left']:.3f}/{r['range_right']:.3f} |"
        )
    ra = report["reference_agreement"]
    lines += [
        "",
        "## Independent reference agreement",
        "",
        f"- core: {ra['core']:.10f}",
        f"- explicit weighted normal equations: {ra['explicit_sums']:.10f} "
        f"(|diff|={ra['abs_diff_explicit']:.2e})",
        f"- scipy.optimize BFGS WLS: {ra['numeric_optimization']:.10f} "
        f"(|diff|={ra['abs_diff_optimized']:.2e})",
        f"- bootstrap-t p={ra['bootstrap_t']['p_value']:.4f}, "
        f"null-t KS p={ra['bootstrap_t']['null_t_ks_pvalue']:.3f}",
        "",
        "## Diagnostic rates",
        "",
        f"- density-sorting flag rate: {report['diagnostic_rates']['density_sorting_flag_rate']:.3f}",
        f"- sparse-boundary unidentified rate: {report['diagnostic_rates']['sparse_unidentified_rate']:.3f}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=200)
    parser.add_argument("--out", type=str, default="results")
    args = parser.parse_args()
    logging_setup.configure_logging("INFO")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.reps)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"replication_{stamp}.json"
    md_path = out / f"replication_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(to_markdown(report))
    latest = out / "latest.md"
    latest.write_text(to_markdown(report))
    print(f"wrote {json_path}\nwrote {md_path}")


if __name__ == "__main__":
    main()
