"""Fixture parsing and validation tests."""

from __future__ import annotations

import json

import pytest

from txmap.errors import ValidationError
from txmap.parsing import parse_fixture


def test_fixture_hand_checked_structure(reference):
    chromosomes, transcripts = reference
    assert chromosomes == {"syn1": 300, "syn2": 750}
    t1 = transcripts["T1_PLUS"]
    assert t1.strand == "+"
    assert [(e.start, e.end) for e in t1.exons] == [
        (100, 130), (160, 180), (210, 240)
    ]
    assert t1.length == 80
    t2 = transcripts["T2_MINUS"]
    assert t2.strand == "-"
    assert t2.length == 75  # 30 + 25 + 20
    t3 = transcripts["T3_ADJACENT"]
    assert t3.length == 25  # 10 + 15, zero-base gap


def write_fixture(tmp_path, payload):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def base_payload():
    return {
        "chromosomes": [{"name": "c", "length": 100, "pattern": "ACGT"}],
        "transcripts": [
            {"id": "X", "chrom": "c", "strand": "+", "exons": [[10, 20]]}
        ],
    }


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ValidationError) as exc:
        parse_fixture(tmp_path / "nope.json")
    assert exc.value.code == "validation_error"


def test_bad_json_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError):
        parse_fixture(p)


def test_overlapping_exons_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["exons"] = [[10, 30], [25, 40]]
    with pytest.raises(ValidationError, match="non-overlapping"):
        parse_fixture(write_fixture(tmp_path, payload))


def test_reversed_exons_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["exons"] = [[30, 40], [10, 20]]
    with pytest.raises(ValidationError):
        parse_fixture(write_fixture(tmp_path, payload))


def test_half_open_zero_or_negative_length_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["exons"] = [[20, 20]]
    with pytest.raises(ValidationError, match="start < end"):
        parse_fixture(write_fixture(tmp_path, payload))


def test_exon_beyond_chromosome_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["exons"] = [[90, 110]]
    with pytest.raises(ValidationError, match="beyond chromosome"):
        parse_fixture(write_fixture(tmp_path, payload))


def test_unknown_strand_and_chromosome_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["strand"] = "."
    with pytest.raises(ValidationError):
        parse_fixture(write_fixture(tmp_path, payload))

    payload = base_payload()
    payload["transcripts"][0]["chrom"] = "ghost"
    with pytest.raises(ValidationError):
        parse_fixture(write_fixture(tmp_path, payload))


def test_adjacent_exons_accepted(tmp_path):
    payload = base_payload()
    payload["transcripts"][0]["exons"] = [[10, 20], [20, 30]]
    _, tx = parse_fixture(write_fixture(tmp_path, payload))
    assert tx["X"].length == 20


def test_duplicate_transcript_id_rejected(tmp_path):
    payload = base_payload()
    payload["transcripts"].append(
        {"id": "X", "chrom": "c", "strand": "-", "exons": [[40, 50]]}
    )
    with pytest.raises(ValidationError, match="duplicate transcript"):
        parse_fixture(write_fixture(tmp_path, payload))
