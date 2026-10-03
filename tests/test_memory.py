"""Peak-memory test: tiled execution must use far less memory than direct.

Each mode runs in its own subprocess (peak RSS is process-wide monotonic),
on an image large enough that the difference dwarfs interpreter overhead:
direct materialises a halo-padded region the size of the whole image plus
the output; tiled materialises only one halo-extended tile at a time.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPE = (2400, 1800)  # ~33 MiB as float64
TILE = (256, 256)
KERNEL = 25


def run_probe(mode: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "app.memprobe",
         "--mode", mode,
         "--shape", *map(str, SHAPE),
         "--tile", *map(str, TILE),
         "--kernel", str(KERNEL)],
        # generous: shared CI hosts can be an order of magnitude slower
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=1800,
    )
    assert proc.returncode == 0, f"memprobe {mode} failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.slow
def test_tiled_peak_memory_below_direct():
    direct = run_probe("direct")
    tiled = run_probe("tiled")
    # Same computation -> same result checksum.
    assert tiled["checksum"] == pytest.approx(direct["checksum"], rel=1e-9)
    # Tiled must stay well under the direct peak: the direct path alone
    # allocates a full extra padded image (~image size) that tiling avoids.
    image_bytes = SHAPE[0] * SHAPE[1] * 8
    assert tiled["peak_rss_bytes"] < direct["peak_rss_bytes"] - 0.5 * image_bytes
