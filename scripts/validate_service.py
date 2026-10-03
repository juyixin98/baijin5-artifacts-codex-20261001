#!/usr/bin/env python3
"""End-to-end validation script (the acceptance runner).

Builds a fresh workspace, synthesizes fixtures, runs tiled jobs across the
acceptance matrix (impulse / edge values / random, irregular tiles, large and
even kernels, all boundary modes, separable filters, interrupt/resume) and
compares each result pixel-by-pixel against the direct scipy reference.

Prints one JSON summary on stdout (last line) with versions, run identity,
per-case metrics and verdicts; progress goes to stderr. Exit code 0 iff
every case passes.

    python scripts/validate_service.py --workspace ./workspace-validation
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from tileconv.contract import BoundaryMode, KernelSpec
from tileconv.errors import InjectedInterrupt
from tileconv.fixtures import make_kernel_weights, synthesize
from tileconv.job import TiledJob
from tileconv.kernel import direct_reference
from tileconv.logging_utils import get_run_logger, versions_snapshot
from tileconv.storage import ImageStore
from tileconv.validation import compare_arrays

ATOL = 1e-9


def build_cases() -> list[dict]:
    g63 = make_kernel_weights("gaussian", 63)
    g64 = make_kernel_weights("gaussian", 64)
    return [
        {"name": "impulse/mirror", "kind": "impulse", "shape": (61, 47),
         "kernel": KernelSpec.dense(np.array([[1.0, 2.0, 1.0],
                                              [2.0, 4.0, 2.0],
                                              [1.0, 2.0, 1.0]]) / 16.0),
         "mode": BoundaryMode.MIRROR, "tile": (16, 16)},
        {"name": "edge_values/constant", "kind": "edge_values", "shape": (40, 36),
         "kernel": KernelSpec.dense(np.ones((3, 5)) / 15.0),
         "mode": BoundaryMode.CONSTANT, "cval": 0.5, "tile": (16, 16)},
        {"name": "edge_values/periodic", "kind": "edge_values", "shape": (40, 36),
         "kernel": KernelSpec.dense(np.ones((3, 5)) / 15.0),
         "mode": BoundaryMode.PERIODIC, "tile": (16, 16)},
        {"name": "random/irregular-tiles+even-kernel", "kind": "random",
         "shape": (103, 89),
         "kernel": KernelSpec.dense(np.arange(1, 25, dtype=np.float64).reshape(6, 4) / 300.0),
         "mode": BoundaryMode.MIRROR, "tile": (37, 29)},
        {"name": "random/large-kernel-63", "kind": "random", "shape": (90, 80),
         "kernel": KernelSpec.dense(np.outer(g63, g63)),
         "mode": BoundaryMode.MIRROR, "tile": (32, 32), "atol": 1e-8},
        {"name": "random/large-even-kernel-64", "kind": "random", "shape": (70, 60),
         "kernel": KernelSpec.dense(np.outer(g64, g64)),
         "mode": BoundaryMode.PERIODIC, "tile": (24, 24), "atol": 1e-8},
        {"name": "random/kernel-larger-than-tile", "kind": "random", "shape": (40, 36),
         "kernel": KernelSpec.dense(np.outer(make_kernel_weights("gaussian", 31),
                                             make_kernel_weights("gaussian", 31))),
         "mode": BoundaryMode.CONSTANT, "tile": (8, 8)},
        {"name": "random/separable-large", "kind": "random", "shape": (96, 84),
         "kernel": KernelSpec.separable(make_kernel_weights("gaussian", 51),
                                        make_kernel_weights("box", 34)),
         "mode": BoundaryMode.MIRROR, "tile": (20, 20)},
        {"name": "ramp/interrupt-resume", "kind": "ramp", "shape": (45, 39),
         "kernel": KernelSpec.dense(make_kernel_weights("asymmetric", 5).reshape(5, 1)
                                    * np.ones((1, 3))),
         "mode": BoundaryMode.MIRROR, "tile": (16, 16), "interrupt_at": 3},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="./workspace-validation")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    store = ImageStore(workspace)
    run_id = uuid.uuid4().hex[:12]
    run_logger = get_run_logger(workspace / "logs", run_id)
    run_logger.log("validation_run_started", versions=versions_snapshot())

    results = []
    failures = 0
    for case in build_cases():
        img = synthesize(case["kind"], case["shape"], seed=case.get("seed", 9))
        spec = store.create_image(img, meta={"kind": case["kind"]})
        kernel = case["kernel"]
        cval = case.get("cval", 0.0)
        job = TiledJob.create(store, workspace / "jobs", spec, kernel,
                              case["mode"], cval=cval, tile_shape=case["tile"],
                              logs_dir=workspace / "logs")
        interrupted = False
        if case.get("interrupt_at"):
            try:
                job.run(fail_after=case["interrupt_at"])
            except InjectedInterrupt:
                interrupted = True
            job = TiledJob(job.state_path, store)
            job.resume()
        else:
            job.run()

        expected = direct_reference(img, kernel, case["mode"], cval=cval)
        atol = case.get("atol", ATOL)
        report = compare_arrays(job.open_output()[:], expected, atol=atol, rtol=0.0)
        entry = {
            "case": case["name"],
            "passed": report.passed,
            "max_abs_diff": report.max_abs_diff,
            "mismatch_count": report.mismatch_count,
            "atol": atol,
            "interrupted_and_resumed": interrupted,
            "image_digest": spec.digest,
            "kernel_digest": kernel.digest(),
            "tiles": len(job.state["tiles"]),
        }
        results.append(entry)
        run_logger.log("validation_case", **entry, basis=report.basis)
        status = "PASS" if report.passed else "FAIL"
        print(f"[{status}] {case['name']}: max_abs_diff={report.max_abs_diff:.3e} "
              f"tiles={entry['tiles']}", file=sys.stderr)
        if not report.passed:
            failures += 1

    summary = {
        "run_id": run_id,
        "versions": versions_snapshot(),
        "cases": results,
        "passed": failures == 0,
        "failures": failures,
        "basis": "per-pixel |tiled - scipy_direct| <= atol; reference: "
                 "scipy.ndimage.convolve native boundary modes",
    }
    run_logger.log("validation_run_finished", passed=summary["passed"],
                   failures=failures)
    print(json.dumps(summary, indent=1))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
