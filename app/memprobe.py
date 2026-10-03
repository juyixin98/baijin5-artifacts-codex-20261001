"""Memory probe entrypoint: ``python -m app.memprobe ...``.

Runs one filtering mode (tiled or direct) in a fresh process and prints the
peak RSS as JSON on the last stdout line.  Comparative memory assertions run
each mode in its own subprocess because peak RSS is process-wide monotonic.

The tiled branch is careful to stay memory-bounded end to end: the checksum
is accumulated in row chunks instead of loading the whole output.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np

from .config import Settings
from .contract import BoundaryMode
from .fixtures import random_kernel, seeded_noise_image
from .jobs import JobSpec, TiledJobRunner
from .kernels import filter_direct
from .memory import peak_rss_bytes


def _chunked_sum(path: Path, shape: tuple[int, int], rows_per_chunk: int = 64) -> float:
    # open_memmap parses the .npy header (np.memmap would map it as data).
    mm = np.lib.format.open_memmap(path, mode="r")
    if mm.shape != shape:
        raise ValueError(f"output shape {mm.shape} != expected {shape}")
    total = 0.0
    for y in range(0, shape[0], rows_per_chunk):
        total += float(mm[y : y + rows_per_chunk].sum())
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tiled", "direct"], required=True)
    parser.add_argument("--shape", type=int, nargs=2, required=True)
    parser.add_argument("--tile", type=int, nargs=2, default=[256, 256])
    parser.add_argument("--kernel", type=int, default=25)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    shape = (args.shape[0], args.shape[1])
    kernel = random_kernel((args.kernel, args.kernel), seed=args.seed)

    if args.mode == "direct":
        img = seeded_noise_image(shape, seed=args.seed)
        out = filter_direct(img, kernel, BoundaryMode.MIRROR)
        checksum = float(out.sum())
    else:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(workspace_dir=Path(tmp), log_dir=Path(tmp))
            spec = JobSpec(
                image={"kind": "noise", "shape": list(shape), "seed": args.seed},
                kernel={
                    "kind": "dense",
                    "weights": kernel.array.tolist(),
                    "anchor": list(kernel.anchor),
                },
                boundary="mirror",
                tile=(args.tile[0], args.tile[1]),
            )
            runner = TiledJobRunner(spec=spec, settings=settings)
            runner.create()
            runner.execute()
            checksum = _chunked_sum(runner.job_dir / "output.npy", shape)

    print(json.dumps({
        "mode": args.mode,
        "shape": shape,
        "tile": args.tile,
        "kernel": args.kernel,
        "peak_rss_bytes": peak_rss_bytes(),
        "checksum": checksum,
    }))


if __name__ == "__main__":
    main()
