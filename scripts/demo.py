#!/usr/bin/env python3
"""Local end-to-end demo: stream TSV through external sort and report depth.

Run from the repository root::

    python scripts/demo.py
    python scripts/demo.py --chunk-size 2   # force multiple spill runs

The demo uses only the synthetic fixtures under ``examples/`` and prints the
per-base depth, segments, histogram and the accept/reject/undetermined ledger.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depthcov.config import Settings  # noqa: E402
from depthcov.engine import Reference, analyze_lines  # noqa: E402
from depthcov.provenance import ProvenanceStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def read_references(path: Path) -> list[Reference]:
    refs: list[Reference] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, length = line.split("\t")[:2]
        refs.append(Reference(name=name, length=int(length)))
    return refs


def render_depth(depth: list[int]) -> str:
    return "".join(str(min(d, 9)) for d in depth)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignments", default=str(ROOT / "examples" / "alignments.tsv"))
    parser.add_argument("--references", default=str(ROOT / "examples" / "references.tsv"))
    parser.add_argument("--chunk-size", type=int, default=3)
    parser.add_argument("--min-mapq", type=int, default=20)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()

    refs = read_references(Path(args.references))
    lines = Path(args.alignments).read_text(encoding="utf-8").splitlines()

    settings = Settings(
        min_mapq=args.min_mapq,
        external_sort_chunk_size=args.chunk_size,
        db_path=args.db,
    )

    store = ProvenanceStore(args.db)
    try:
        report = analyze_lines(
            lines, refs, settings,
            request_id="demo-request",
            store=store,
            use_external_sort=True,
        )

        print(f"request_id={report.request_id} run_id={report.run_id}")
        print(
            f"input={report.input_count} accepted={len(report.accepted)} "
            f"rejected={len(report.rejected)} "
            f"undetermined={len(report.undetermined)} "
            f"external_sorted={report.external_sorted}"
        )
        print()
        for name, result in report.results.items():
            depth = result.per_base_depth.tolist()
            print(f"== {name} (length {result.ref_length}) ==")
            print("position:  " + "".join(str(i % 10) for i in range(result.ref_length)))
            print("depth:     " + render_depth(depth))
            print("segments:  " + ", ".join(
                f"[{s.start},{s.end})d{s.depth}" for s in result.segments
            ))
            print(f"histogram: {dict(sorted(result.histogram.items()))}")
            print(
                f"weighted_length={result.weighted_length} "
                f"covered_bases={result.covered_bases} "
                f"sum(hist)={sum(result.histogram.values())}"
            )
            print()

        print("== ledger ==")
        for d in report.diagnostics:
            print(
                f"{d['outcome']:<12} {d['record_id']:<10} "
                f"{d['reason']:<18} {d['message']}"
            )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
