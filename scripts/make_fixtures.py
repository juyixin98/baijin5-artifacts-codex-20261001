#!/usr/bin/env python3
"""Regenerate the local synthetic fixtures deterministically.

The reference contig is the pattern "ACGT" repeated (base(i) = "ACGT"[i % 4]),
so every expected base in the test-suite can be derived by hand. Transcript
exon structures are fixed literals, not generated.

Usage: python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # allow running as a plain script

from txmap.reference import synthetic_sequence, write_fasta
from txmap.models import Contig

FIXTURES = ROOT / "fixtures"

CONTIG_NAME = "chrSyn1"
CONTIG_LENGTH = 120

TRANSCRIPTS = {
    "contig": CONTIG_NAME,
    "coordinate_system": "0-based half-open [start, end)",
    "transcripts": [
        {"id": "txA", "gene": "geneA", "contig": CONTIG_NAME, "strand": "+",
         "exons": [[10, 20], [30, 45], [60, 70]]},
        {"id": "txB", "gene": "geneB", "contig": CONTIG_NAME, "strand": "-",
         "exons": [[15, 25], [40, 50], [80, 100]]},
        {"id": "txC", "gene": "geneC", "contig": CONTIG_NAME, "strand": "+",
         "exons": [[12, 18], [65, 75]]},
    ],
}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    contig = Contig(name=CONTIG_NAME, sequence=synthetic_sequence(CONTIG_LENGTH))
    write_fasta({CONTIG_NAME: contig}, FIXTURES / "reference.fa")
    (FIXTURES / "transcripts.json").write_text(
        json.dumps(TRANSCRIPTS, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURES / 'reference.fa'} and {FIXTURES / 'transcripts.json'}")


if __name__ == "__main__":
    main()
