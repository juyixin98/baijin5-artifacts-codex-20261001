#!/usr/bin/env python3
"""End-to-end validation script.

Runs a matrix of cases (impulse / edge / noise images x boundary modes x
odd/even/large kernels x irregular tiles) and compares, pixel by pixel:

  tiled output  vs  single-pass direct (app.kernels)
  tiled output  vs  scipy.ndimage reference (app.reference, independent)

Exit code is non-zero if any case fails, so this doubles as a CI gate.
Every line carries the case identity and the judgement basis (max-abs error
vs tolerance), never a bare "ok".
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# Allow running as ``python scripts/validate.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.contract import BoundaryMode  # noqa: E402
from app.jobs import (  # noqa: E402
    JobSpec,
    JobStateError,
    TiledJobRunner,
    build_image,
    build_kernel,
)
from app.kernels import filter_direct, filter_separable_direct  # noqa: E402
from app.logging_utils import versions  # noqa: E402
from app.reference import scipy_reference  # noqa: E402


def case_matrix() -> list[dict]:
    cases: list[dict] = []
    boundaries = ["mirror", "constant", "periodic"]
    images = [
        {"kind": "impulse", "shape": [103, 97]},
        {"kind": "step_edge", "shape": [103, 97], "axis": 1},
        {"kind": "tagged_border", "shape": [64, 48], "border": 2},
        {"kind": "noise", "shape": [103, 97], "seed": 11},
    ]
    kernels = [
        {"kind": "box", "k": 3},
        {"kind": "gaussian", "k": 7, "sigma": 1.4},
        {"kind": "separable_gaussian", "k": 9, "sigma": 2.0},
        {"kind": "gaussian", "k": 31, "sigma": 5.0},  # large kernel
    ]
    for boundary in boundaries:
        for image in images:
            for kernel in kernels:
                cases.append({
                    "image": image,
                    "kernel": kernel,
                    "boundary": boundary,
                    "cval": 0.25,
                    "tile": [32, 32],  # irregular: 103x97 is not a multiple
                })
    # Even kernels with explicit anchors on both parities of tile size.
    cases.append({
        "image": {"kind": "noise", "shape": [70, 54], "seed": 3},
        "kernel": {"kind": "dense", "weights": [[1.0, -1.0], [0.5, 0.25]],
                   "anchor": [1, 1]},
        "boundary": "mirror", "cval": 0.0, "tile": [16, 16],
    })
    cases.append({
        "image": {"kind": "noise", "shape": [70, 54], "seed": 4},
        "kernel": {"kind": "dense",
                   "weights": [[0.1] * 4] * 4, "anchor": [2, 2]},
        "boundary": "periodic", "cval": 0.0, "tile": [17, 13],
    })
    return cases


def run_case(spec_dict: dict, settings: Settings, tol: float) -> dict:
    spec = JobSpec.from_dict(spec_dict)
    runner = TiledJobRunner(spec=spec, settings=settings)
    try:
        runner.create()
    except JobStateError:
        # identical spec already materialised in this workspace; reuse it
        pass
    runner.execute()
    tiled = runner.result_array()

    image = build_image(spec.image, settings)
    kernel = build_kernel(spec.kernel, settings)
    boundary = BoundaryMode.parse(spec.boundary)
    if spec.kernel.get("kind") in ("separable", "separable_gaussian"):
        direct = filter_separable_direct(image.data, kernel, boundary, spec.cval)
    else:
        direct = filter_direct(image.data, kernel, boundary, spec.cval)
    ref = scipy_reference(image.data, kernel, boundary, spec.cval)

    import numpy as np

    err_direct = float(np.max(np.abs(tiled - direct)))
    err_scipy = float(np.max(np.abs(tiled - ref)))
    ok = err_direct <= tol and err_scipy <= tol
    return {
        "case": {
            "image": spec.image["kind"],
            "kernel": spec.kernel["kind"],
            "boundary": spec.boundary,
            "tile": list(spec.tile),
        },
        "job_id": runner.compute_job_id(),
        "err_tiled_vs_direct": err_direct,
        "err_tiled_vs_scipy": err_scipy,
        "tolerance": tol,
        "status": "pass" if ok else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON lines")
    args = parser.parse_args()

    tol = Settings().compare_tolerance
    print(f"versions: {json.dumps(versions())}", file=sys.stderr)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(workspace_dir=Path(tmp), log_dir=Path(tmp))
        for i, case in enumerate(case_matrix(), start=1):
            result = run_case(case, settings, tol)
            if args.json:
                print(json.dumps(result))
            else:
                c = result["case"]
                print(
                    f"[{i:3d}] {result['status'].upper():4s} "
                    f"img={c['image']:<14s} kernel={c['kernel']:<19s} "
                    f"bnd={c['boundary']:<8s} "
                    f"err(direct)={result['err_tiled_vs_direct']:.3e} "
                    f"err(scipy)={result['err_tiled_vs_scipy']:.3e} "
                    f"tol={result['tolerance']:.1e} job={result['job_id']}"
                )
            if result["status"] != "pass":
                failures += 1
    total = len(case_matrix())
    print(f"{total - failures}/{total} cases passed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
