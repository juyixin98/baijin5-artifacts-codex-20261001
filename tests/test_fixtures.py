"""Integration tests driven by the bundled synthetic fixture files.

These pin concrete results against known planted coordinates rather than
merely checking that endpoints/functions can be called.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from miniseed.sequence import parse_fasta_records, parse_sequence
from miniseed.service import MiniseedService
from miniseed.store import SeedStore
from miniseed.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
K, W = 9, 5
PLANTED_OFFSET = 50  # read_exact_28bp is reference[50:78]


@pytest.fixture
def indexed_service(tmp_path: Path):
    settings = Settings(k=K, w=W, db_path=tmp_path / "fixture.db")
    ref_doc = (FIXTURES / "reference_syn.txt").read_text()
    _, ref_body = parse_fasta_records(ref_doc)[0]
    ref = parse_sequence(ref_body, name="reference").sequence
    with SeedStore(settings.db_path) as store:
        svc = MiniseedService(store, settings)
        svc.index_reference("fixture", ref, k=K, w=W)
        yield svc, parse_fasta_records((FIXTURES / "reads_syn.txt").read_text())


def _read_by_name(records, needle: str) -> str:
    for header, body in records:
        if needle in header:
            return parse_sequence(body, name=needle).sequence
    raise AssertionError(f"read {needle} not present in fixture")


def test_exact_fixture_read_anchors_at_planted_offset(indexed_service):
    svc, records = indexed_service
    read = _read_by_name(records, "read_exact_28bp")
    q = svc.query_read("fixture", read, k=K, w=W)
    forward = [loc for loc in q.locations if loc.strand == "+"]
    assert forward, "expected a forward-strand candidate"
    top = forward[0]
    # The collinear diagonal of a substring query equals its origin offset.
    assert top.diagonal == PLANTED_OFFSET
    assert top.hit_count >= 5


def test_reverse_complement_fixture_read_uses_reflected_diagonal(indexed_service):
    svc, records = indexed_service
    read = _read_by_name(records, "read_revcomp_28bp")
    q = svc.query_read("fixture", read, k=K, w=W)
    reverse = [loc for loc in q.locations if loc.strand == "-"]
    assert reverse, "expected a reverse-strand candidate"
    # Reflected diagonal for origin O and read length L is O + (L-1)-ish;
    # the top reverse hit must cover the planted locus region.
    top = reverse[0]
    assert PLANTED_OFFSET <= top.ref_start <= PLANTED_OFFSET + 25


def test_tail_single_window_fixture_read(indexed_service):
    svc, records = indexed_service
    read = _read_by_name(records, "read_short_13bp")
    assert len(read) == K + W - 1
    q = svc.query_read("fixture", read, k=K, w=W)
    assert q.windows == 1
    assert q.query_seed_count == 1
    # The 13 bp read is reference[60:73], so at least one hit near offset 60.
    assert any(
        55 <= loc.ref_start <= 70 for loc in q.locations
    )


def test_novel_fixture_read_has_no_recall(indexed_service):
    svc, records = indexed_service
    read = _read_by_name(records, "read_novel_27bp")
    q = svc.query_read("fixture", read, k=K, w=W)
    assert q.total_hits == 0
