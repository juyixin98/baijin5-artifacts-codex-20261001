#!/usr/bin/env python3
"""Local demo: parse the synthetic fixture, run the coverage pipeline
in-process, and print the segmentation, histogram and decision audit.

Usage:
    python scripts/demo.py [--fixture PATH] [--chunk-size N]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from coverage_depth.config import PipelineConfig
from coverage_depth.diagnostics import new_request_id
from coverage_depth.parsing import iter_alignment_lines, parse_alignment_line
from coverage_depth.pipeline import run_pipeline

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "alignments_small.tsv"
)
REF_NAME = "chrS"
REF_LENGTH = 40


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--chunk-size", type=int, default=50_000,
                        help="external-sort chunk size; small values force spilling")
    args = parser.parse_args()

    request_id = new_request_id()
    records = [
        parse_alignment_line(line, i)
        for i, line in iter_alignment_lines(args.fixture.read_text())
    ]
    print(f"[demo] request_id={request_id} parsed {len(records)} records "
          f"from {args.fixture}")

    with tempfile.TemporaryDirectory() as spill:
        result = run_pipeline(
            records, REF_NAME, REF_LENGTH,
            PipelineConfig(sort_chunk_size=args.chunk_size),
            spill_dir=spill,
        )

    print(f"[demo] reference {result.ref} length={result.ref_length}")
    print("[demo] segments (0-based half-open, depth >= 1):")
    for seg in result.segments:
        print(f"         [{seg.start:>3}, {seg.end:>3})  depth={seg.depth}")
    print(f"[demo] histogram (depth -> bases): "
          f"{dict(sorted(result.histogram.items()))}")
    print(f"[demo] covered_bases={result.covered_bases} "
          f"weighted_bases={result.weighted_bases} "
          f"mean_depth={result.mean_depth:.3f}")
    print("[demo] decisions:")
    for d in result.decisions:
        print(f"         #{d.record_index} {d.read_id:<4} "
              f"{d.status.value:<11} {d.reason.value:<14} {d.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
