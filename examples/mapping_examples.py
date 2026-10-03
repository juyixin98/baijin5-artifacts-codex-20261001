#!/usr/bin/env python3
"""Library usage examples (run without starting the HTTP server).

    PYTHONPATH=src python examples/mapping_examples.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from txmap.errors import MappingError  # noqa: E402
from txmap.parsing import parse_fixture  # noqa: E402
from txmap.mapping import CoordinateMapper  # noqa: E402
from txmap.sequence import complement, transcript_sequence  # noqa: E402
from txmap.service import MappingService  # noqa: E402
from txmap.storage import Repository  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "transcripts.json"


def show(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    chromosomes, transcripts = parse_fixture(FIXTURE)
    patterns = {"syn1": "ACGT", "syn2": "ACGT"}

    plus = CoordinateMapper(transcripts["T1_PLUS"])
    minus = CoordinateMapper(transcripts["T2_MINUS"])

    # 1) point round trip on the minus strand
    g = minus.tx_to_genomic_point(0).genomic_position
    p = minus.genomic_to_tx_point(g).tx_position
    show("minus-strand round trip tx0", {"genomic": g, "back_to_tx": p})

    # 2) a transcript interval crossing two introns splits into 3 fragments;
    #    the intronic bases are never inserted -> total length is conserved
    split = plus.tx_to_genomic_interval(25, 55)
    show("plus tx[25,55) splits across 3 exons", {
        "requested_length": split.length,
        "mapped_length": split.mapped_length,
        "fragments": [
            {"tx": [f.tx_start, f.tx_end], "genomic": [f.genomic_start, f.genomic_end]}
            for f in split.fragments
        ],
    })

    # 3) intronic positions are rejected, not snapped
    try:
        plus.genomic_to_tx_point(145)
    except MappingError as exc:
        show("intronic position rejected", exc.to_dict())

    # 4) minus-strand transcript base = complement of mapped reference base
    tx0_g = minus.tx_to_genomic_point(0).genomic_position
    ref_base = patterns["syn2"][tx0_g % 4]
    seq = transcript_sequence(transcripts["T2_MINUS"], patterns["syn2"])
    show("minus-strand base orientation", {
        "genomic_position": tx0_g,
        "reference_base": ref_base,
        "transcript_base": seq[0],
        "complement_matches": complement(ref_base) == seq[0],
    })

    # 5) service layer with SQLite provenance
    with tempfile.TemporaryDirectory() as d:
        repo = Repository(Path(d) / "example.sqlite3")
        repo.replace_reference(chromosomes, patterns, transcripts)
        svc = MappingService(repo)
        out, _ = svc.map_point("T1_PLUS", "tx_to_genomic", 30, "example-req")
        show("service call with audit id", out)
        show("audit trail", svc.audit_trail("example-req"))
        repo.close()


if __name__ == "__main__":
    main()
