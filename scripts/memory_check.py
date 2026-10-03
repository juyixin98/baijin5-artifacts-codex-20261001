#!/usr/bin/env python3
"""Peak-memory check for the tiled engine.

Runs tiled jobs over memmap-backed images of one or more sizes in this
(sub)process and reports, per size:
- tracemalloc peak (Python-side allocations; bounded by tile, not image)
- VmHWM (process high-water RSS, includes memmap pages touched)

Output: one JSON summary line on stdout (last line). Human-readable progress
goes to stderr. Used by tests/test_memory.py and runnable standalone:

    python scripts/memory_check.py --sizes 1200x900,2400x1800 --tile 256 --kernel 31
"""

from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from tileconv.contract import BoundaryMode, KernelSpec
from tileconv.fixtures import make_kernel_weights, synthesize
from tileconv.job import TiledJob
from tileconv.logging_utils import get_run_logger, versions_snapshot
from tileconv.storage import ImageStore


def vmhwm_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def parse_size(text: str) -> tuple[int, int]:
    h, w = text.lower().split("x")
    return int(h), int(w)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--sizes", default="1200x900,2400x1800")
    parser.add_argument("--tile", type=int, default=256)
    parser.add_argument("--kernel", type=int, default=31)
    parser.add_argument("--cap-mb", type=float, default=64.0,
                        help="cap for tracemalloc peak (tile-bounded allocations)")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    store = ImageStore(workspace)
    run_id = uuid.uuid4().hex[:12]
    run_logger = get_run_logger(workspace / "logs", run_id)

    g = make_kernel_weights("gaussian", args.kernel)
    kernel = KernelSpec.dense(np.outer(g, g))

    runs = []
    for text in args.sizes.split(","):
        h, w = parse_size(text)
        img = synthesize("random", (h, w), seed=123)
        spec = store.create_image(img.astype(np.float64),
                                  meta={"kind": "random", "seed": 123})
        del img
        job = TiledJob.create(store, workspace / "jobs", spec, kernel,
                              BoundaryMode.MIRROR, tile_shape=(args.tile, args.tile),
                              logs_dir=workspace / "logs")
        tracemalloc.start()
        job.run()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        entry = {
            "size": [h, w],
            "pixels": h * w,
            "tracemalloc_peak_mb": peak / 1e6,
            "vmhwm_mb": vmhwm_mb(),
            "image_digest": spec.digest,
            "kernel_digest": kernel.digest(),
            "tiles": len(job.state["tiles"]),
        }
        runs.append(entry)
        run_logger.log("memory_check_run", **entry)
        print(f"[memory_check] {text}: tracemalloc_peak={entry['tracemalloc_peak_mb']:.1f}MB "
              f"vmhwm={entry['vmhwm_mb']:.1f}MB tiles={entry['tiles']}", file=sys.stderr)

    summary = {
        "run_id": run_id,
        "cap_mb": args.cap_mb,
        "tile": args.tile,
        "kernel_size": args.kernel,
        "versions": versions_snapshot(),
        "runs": runs,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
