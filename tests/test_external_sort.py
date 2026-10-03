"""External sort: bounded memory runs, global order, spill files cleaned."""

import os

from depthcov.external_sort import (
    RecordCodecError,
    decode_record,
    encode_record,
    external_sort,
    parse_tsv_file,
    sort_key,
)
from depthcov.models import Alignment


def _records(n):
    # Descending / shuffled-ish order so sorting actually matters.
    return [
        Alignment(f"q{i:04d}", "chr1", (n - i) % 17, f"{2 + (i % 5)}M",
                  mapq=60)
        for i in range(n)
    ]


def test_codec_roundtrip_preserves_all_fields():
    aln = Alignment("q1", "chrX", 7, "3M2D1M", mapq=42,
                    query_length=9, is_duplicate=True)
    again = decode_record(encode_record(aln))
    assert again == aln


def test_external_sort_globally_orders_multiple_runs(tmp_path):
    records = _records(207)
    out = list(
        external_sort(iter(records), chunk_size=25, tmp_dir=str(tmp_path))
    )
    keys = [sort_key(r) for r in out]
    assert keys == sorted(keys)
    assert len(out) == 207
    # Same multiset of qnames.
    assert sorted(r.query_name for r in out) == sorted(
        r.query_name for r in records
    )


def test_external_sort_cleans_spill_files(tmp_path):
    list(external_sort(iter(_records(100)), chunk_size=10,
                       tmp_dir=str(tmp_path)))
    leftovers = [
        f for f in os.listdir(tmp_path) if f.startswith("depthcov-run-")
    ]
    assert leftovers == []


def test_empty_input_yields_nothing(tmp_path):
    assert list(external_sort(iter([]), tmp_dir=str(tmp_path))) == []


def test_single_element_chunks_still_merge(tmp_path):
    out = list(external_sort(iter(_records(5)), chunk_size=1,
                             tmp_dir=str(tmp_path)))
    assert [sort_key(r) for r in out] == sorted(sort_key(r) for r in out)


def test_invalid_chunk_size_rejected():
    import pytest
    with pytest.raises(ValueError):
        list(external_sort(iter(_records(1)), chunk_size=0))


def test_decode_error_categories():
    import pytest
    with pytest.raises(RecordCodecError):
        decode_record("only\ttwo")  # too few fields
    with pytest.raises(RecordCodecError):
        decode_record("q\tchr\tNOTINT\t60\t\t+\trg\t0\t5M")
    with pytest.raises(RecordCodecError):
        decode_record("q\tchr\t1\t60\t\t?\trg\t0\t5M")
    with pytest.raises(RecordCodecError):
        decode_record("q\tchr\t1\t60\t\t+\trg\tX\t5M")


def test_parse_tsv_file_skips_headers(tmp_path):
    p = tmp_path / "in.tsv"
    p.write_text(
        "# header\nq1\tchr1\t0\t60\t\t+\td\t0\t5M\n\n"
        "q2\tchr1\t3\t30\t\t-\td\t0\t2M1D2M\n",
        encoding="utf-8",
    )
    recs = list(parse_tsv_file(str(p)))
    assert [r.query_name for r in recs] == ["q1", "q2"]
    assert recs[1].strand.value == "-"
