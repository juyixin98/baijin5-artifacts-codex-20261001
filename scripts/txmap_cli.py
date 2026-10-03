#!/usr/bin/env python3
"""Command-line client for the mapping engine (no server required).

Examples:
    PYTHONPATH=src python scripts/txmap_cli.py info T1_PLUS
    PYTHONPATH=src python scripts/txmap_cli.py t2g T1_PLUS --interval 25 55
    PYTHONPATH=src python scripts/txmap_cli.py g2t T2_MINUS --interval 525 530
    PYTHONPATH=src python scripts/txmap_cli.py t2g T2_MINUS --point 0
    PYTHONPATH=src python scripts/txmap_cli.py g2t T1_PLUS --point 145
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from txmap.errors import MappingError  # noqa: E402
from txmap.mapping import CoordinateMapper  # noqa: E402
from txmap.parsing import parse_fixture  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "transcripts.json"


def _load_mapper(tx_id: str) -> CoordinateMapper:
    _, transcripts = parse_fixture(FIXTURE)
    if tx_id not in transcripts:
        raise MappingError(
            f"unknown transcript {tx_id!r}; known: {sorted(transcripts)}"
        )
    return CoordinateMapper(transcripts[tx_id])


def _fragment(f) -> dict:
    return {
        "tx": [f.tx_start, f.tx_end],
        "genomic": [f.genomic_start, f.genomic_end],
        "length": f.length,
        "exon_index": f.exon_index,
        "genomic_order": f.genomic_order,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="txmap coordinate mapper")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_map_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("transcript_id")
        grp = p.add_mutually_exclusive_group(required=True)
        grp.add_argument("--point", type=int)
        grp.add_argument("--interval", nargs=2, type=int, metavar=("START", "END"))

    add_map_args(sub.add_parser("t2g", help="transcript -> genomic"))
    add_map_args(sub.add_parser("g2t", help="genomic -> transcript"))
    p_info = sub.add_parser("info", help="show transcript metadata")
    p_info.add_argument("transcript_id")
    p_list = sub.add_parser("list", help="list transcripts")
    p_list.add_argument("none", nargs="?")

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            _, transcripts = parse_fixture(FIXTURE)
            for tid, tx in transcripts.items():
                print(f"{tid:14s} {tx.chrom:5s} {tx.strand} len={tx.length:3d} "
                      f"exons={[ [e.start, e.end] for e in tx.exons ]}")
            return 0

        mapper = _load_mapper(args.transcript_id)
        if args.command == "info":
            tx = mapper.transcript
            print(json.dumps({
                "transcript_id": tx.transcript_id,
                "chrom": tx.chrom,
                "strand": tx.strand,
                "mature_length": tx.length,
                "genomic_span": list(tx.genomic_span()),
                "exons": [[e.start, e.end] for e in tx.exons],
            }, indent=2))
            return 0

        to_genomic = args.command == "t2g"
        if args.point is not None:
            result = (
                mapper.tx_to_genomic_point(args.point)
                if to_genomic else mapper.genomic_to_tx_point(args.point)
            )
            print(json.dumps({
                "tx_position": result.tx_position,
                "genomic_position": result.genomic_position,
                "exon_index": result.exon_index,
                "strand": result.strand,
            }, indent=2))
        else:
            start, end = args.interval
            result = (
                mapper.tx_to_genomic_interval(start, end)
                if to_genomic else mapper.genomic_to_tx_interval(start, end)
            )
            print(json.dumps({
                "tx_interval": [result.tx_start, result.tx_end],
                "length": result.length,
                "mapped_length": result.mapped_length,
                "fragment_count": result.fragment_count,
                "fragments": [_fragment(f) for f in result.fragments],
            }, indent=2))
        return 0
    except MappingError as exc:
        print(json.dumps({"error": exc.to_dict()}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
