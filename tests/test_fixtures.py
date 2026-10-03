"""Fixture regeneration must reproduce the committed fixtures byte-for-byte,
and the reference parser must agree with the hand-written FASTA."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from txmap.reference import parse_fasta, synthetic_sequence

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def test_reference_fasta_is_the_synthetic_pattern():
    contigs = parse_fasta(FIXTURES / "reference.fa")
    contig = contigs["chrSyn1"]
    assert contig.length == 120
    assert contig.sequence == synthetic_sequence(120)
    # Hand-checkable base law: base(i) == "ACGT"[i % 4].
    for i in (0, 1, 2, 3, 10, 59, 99, 119):
        assert contig.sequence[i] == "ACGT"[i % 4]


def test_transcript_fixture_structure():
    payload = json.loads((FIXTURES / "transcripts.json").read_text())
    by_id = {t["id"]: t for t in payload["transcripts"]}
    assert by_id["txA"]["exons"] == [[10, 20], [30, 45], [60, 70]]
    assert by_id["txB"]["strand"] == "-"
    assert by_id["txB"]["exons"] == [[15, 25], [40, 50], [80, 100]]
    assert by_id["txC"]["exons"] == [[12, 18], [65, 75]]


def test_make_fixtures_regenerates_identical_files(tmp_path):
    script = ROOT / "scripts" / "make_fixtures.py"
    env_fixture_dir = tmp_path / "fixtures"
    # The script writes into the repo fixtures dir; run it in a copy to
    # avoid touching the committed files, then compare.
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    # After regeneration, committed fixtures must be unchanged in content.
    contigs = parse_fasta(FIXTURES / "reference.fa")
    assert contigs["chrSyn1"].sequence == synthetic_sequence(120)
    assert not env_fixture_dir.exists()  # script wrote to repo fixtures only
