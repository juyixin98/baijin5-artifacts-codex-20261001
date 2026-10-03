"""Peak-memory acceptance check (subprocess-based).

Runs ``scripts/memory_check.py`` as a subprocess so the peak measurements
are not polluted by the pytest process. The script executes tiled jobs over
memmap-backed images of two different sizes and reports tracemalloc peaks
(Python-side allocations, bounded by tile size) plus VmHWM.

Honest scope note: this environment does not have an image larger than RAM;
what is verified here is that engine allocations stay bounded while the
image size grows (the memmap design keeps image data out of the Python
allocator). The true >RAM run is listed in README's not-run section.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def memory_report(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("memcheck") / "workspace"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "memory_check.py"),
         "--workspace", str(workspace),
         "--sizes", "1200x900,2400x1800",
         "--tile", "256", "--kernel", "31"],
        capture_output=True, text=True, env=env, timeout=1800,
    )
    assert proc.returncode == 0, f"memory_check failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestPeakMemory:
    def test_python_allocations_bounded_by_tile_not_image(self, memory_report):
        runs = memory_report["runs"]
        assert len(runs) == 2
        small, large = runs[0], runs[1]
        # image area quadruples; python-side peak must stay nearly flat
        assert large["pixels"] == pytest.approx(small["pixels"] * 4, rel=0.05)
        assert large["tracemalloc_peak_mb"] < small["tracemalloc_peak_mb"] * 1.5

    def test_python_allocations_under_cap(self, memory_report):
        cap_mb = memory_report["cap_mb"]
        for run in memory_report["runs"]:
            assert run["tracemalloc_peak_mb"] < cap_mb, (
                f"tile allocations {run['tracemalloc_peak_mb']}MB exceed cap {cap_mb}MB"
            )

    def test_report_is_attributable(self, memory_report):
        assert memory_report["run_id"]
        assert "numpy" in memory_report["versions"]
        for run in memory_report["runs"]:
            assert run["image_digest"]
            assert run["kernel_digest"]
            assert run["vmhwm_mb"] > 0
