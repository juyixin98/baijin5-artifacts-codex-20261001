"""Run the four contract scenarios locally without HTTP.

Prints the explicit status, jump estimate, CI, effective sample and
identification range for each known-truth DGP, so the backend can be
verified end to end with a single command::

    python3 examples/run_local.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    discrete_runner,
    no_jump,
    sharp_jump,
    sparse_boundary,
)
from app.pipeline import run_analysis

SETTINGS = Settings(
    db_path=":memory:",
    min_obs_per_side=10,
    bootstrap_reps=499,
    default_alpha=0.05,
    log_level="WARNING",
)


def analyse(dgp, *, bandwidth: float | None = 0.25) -> None:
    method = (
        BandwidthMethod.MANUAL if bandwidth is not None else BandwidthMethod.IK_ROT
    )
    req = RDRequest(
        input_label=f"example:{dgp.name}",
        data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(dgp.x, dgp.y)],
        cutoff=0.0,
        bandwidth_method=method,
        bandwidth=bandwidth,
        inference=InferenceMethod.HC3,
        bootstrap_reps=499,
        bootstrap_seed=1234,
    )
    r = run_analysis(req, SETTINGS)
    print(f"\n=== {dgp.name} (true tau = {dgp.tau}) ===")
    print(f"status      : {r.status.value}")
    if r.status is RunStatus.OK and r.estimate is not None:
        e = r.estimate
        print(f"tau         : {e.tau:+.4f}")
        print(
            f"HC3 95% CI  : [{e.ci_low:+.4f}, {e.ci_high:+.4f}]  "
            f"p={e.p_value:.4g}"
        )
        if r.bootstrap is not None:
            print(
                f"wild boot   : p={r.bootstrap.p_value:.4g}  "
                f"CI=[{r.bootstrap.ci_low:+.4f},{r.bootstrap.ci_high:+.4f}]"
            )
        print(
            f"eff N (L/R) : {r.left_fit.effective_n:.1f} / "
            f"{r.right_fit.effective_n:.1f}   bandwidth h={r.bandwidth.left:.4f}"
        )
    else:
        print(f"reason      : {r.error_code.value if r.error_code else '-'} :: "
              f"{r.error_message}")
    for d in r.diagnostics:
        if d.severity.value != "info":
            print(f"[{d.severity.value:7s}] {d.code.value}: {d.message}")


def main() -> None:
    analyse(sharp_jump())
    analyse(no_jump())
    analyse(density_sorting(n=6000), bandwidth=0.4)
    analyse(discrete_runner(), bandwidth=0.3)
    analyse(sparse_boundary(), bandwidth=None)  # IK ROT -> unidentified


if __name__ == "__main__":
    main()
