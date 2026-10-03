"""End-to-end demo: estimate known FIR channels from synthetic fixtures.

Run from the repository root:

    python3 examples/estimate_demo.py

Prints recovered coefficients vs. ground truth for the clean, noisy,
narrowband and delay-misaligned fixtures, and writes a replayable JSONL
run log to examples/run_log.jsonl.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from fir_backend.contracts import EstimateParams
from fir_backend.estimator import estimate_fir
from fir_backend.fixtures import make_fixture
from fir_backend.runlog import RunLogger, file_sink

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.jsonl")


def show(title: str, result, truth: np.ndarray) -> None:
    diag = result.diagnostics
    h_est = result.coefficients_array()
    err = float(np.max(np.abs(h_est - truth))) if len(h_est) == len(truth) else float("nan")
    print(f"\n=== {title} (run_id={result.run_id}) ===")
    print(f"  true taps : {np.array2string(truth, precision=4)}")
    print(f"  estimated : {np.array2string(h_est, precision=4)}")
    print(f"  max |err| : {err:.3e}")
    print(
        f"  rank={diag.rank} effective_rank={diag.effective_rank} "
        f"cond={diag.condition_number:.3e} identifiable={diag.identifiable}"
    )
    if diag.unidentifiable_reasons:
        for reason in diag.unidentifiable_reasons:
            print(f"  ! {reason}")
    print(f"  train_rmse={diag.train_rmse:.4e} holdout_rmse={_fmt(diag.holdout_rmse)}")


def _fmt(value: float | None) -> str:
    return "disabled" if value is None else f"{value:.4e}"


def main() -> None:
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    logger = RunLogger(sink=file_sink(LOG_PATH))

    fixture = make_fixture("clean")
    show(
        "clean, white excitation, no noise",
        estimate_fir(
            fixture.excitation,
            fixture.response,
            EstimateParams(model_order=8),
            logger=logger,
        ),
        fixture.true_coefficients,
    )

    fixture = make_fixture("noisy", noise_std=0.05)
    show(
        "noisy, white excitation, sigma=0.05, ridge=1e-3",
        estimate_fir(
            fixture.excitation,
            fixture.response,
            EstimateParams(model_order=8, regularization=1e-3),
            logger=logger,
        ),
        fixture.true_coefficients,
    )

    fixture = make_fixture("narrowband")
    show(
        "narrowband excitation (spectrally degenerate)",
        estimate_fir(
            fixture.excitation,
            fixture.response,
            EstimateParams(model_order=8),
            logger=logger,
        ),
        fixture.true_coefficients,
    )

    fixture = make_fixture("delayed", delay=5)
    show(
        "delayed response, explicit delay=5",
        estimate_fir(
            fixture.excitation,
            fixture.response,
            EstimateParams(model_order=8, delay=5),
            logger=logger,
        ),
        fixture.true_coefficients,
    )

    print(f"\nrun log written to {LOG_PATH}")


if __name__ == "__main__":
    main()
