"""SQLite provenance: persisted rows independently reproduce totals."""

from depthcov.config import Settings
from depthcov.engine import Reference, analyze
from depthcov.models import Alignment
from depthcov.provenance import ProvenanceStore


def _sample():
    return [
        Alignment("r1", "chr1", 0, "4M1D4M", mapq=60),
        Alignment("r2", "chr1", 2, "5M", mapq=60),
        Alignment("low", "chr1", 0, "5M", mapq=1),
    ]


def test_run_and_records_are_persisted(tmp_path):
    db = str(tmp_path / "prov.db")
    with ProvenanceStore(db) as store:
        report = analyze(
            _sample(), [Reference("chr1", 20)], Settings(),
            request_id="req-test-1", store=store,
        )
        run = store.get_run(report.run_id)
        assert run["request_id"] == "req-test-1"
        assert run["input_count"] == 3
        assert run["accepted_count"] == 2
        assert run["rejected_count"] == 1

        rows = list(store.iter_records(report.run_id))
        by_q = {r["qname"]: r for r in rows}
        assert by_q["r1"]["cigar"] == "4M1D4M"
        assert by_q["r1"]["accepted"] == 1
        assert by_q["r1"]["covered_bases"] == 8
        assert by_q["low"]["accepted"] == 0
        assert by_q["low"]["reason"] == "low_mapq"


def test_segments_and_histogram_read_back_and_recompute(tmp_path):
    db = str(tmp_path / "prov.db")
    with ProvenanceStore(db) as store:
        report = analyze(
            _sample(), [Reference("chr1", 20)], Settings(), store=store,
        )
        rid = report.run_id
        seg_rows = store.get_segments(rid, "chr1")
        # Recompute weighted length purely from persisted segment rows.
        from_segments = sum(
            (r["end"] - r["start"]) * r["depth"] for r in seg_rows
        )
        assert from_segments == report.results["chr1"].weighted_length

        hist = store.get_histogram(rid, "chr1")
        assert sum(hist.values()) == 20
        from_hist = sum(d * c for d, c in hist.items())
        assert from_hist == report.results["chr1"].weighted_length

        ref_row = store.get_ref_result(rid, "chr1")
        assert ref_row["covered_bases"] == report.results["chr1"].covered_bases


def test_unknown_run_id_raises_keyerror(tmp_path):
    with ProvenanceStore(str(tmp_path / "p.db")) as store:
        import pytest
        with pytest.raises(KeyError):
            store.get_run("does-not-exist")


def test_persistence_survives_reopen(tmp_path):
    db = str(tmp_path / "persist.db")
    with ProvenanceStore(db) as store:
        report = analyze(
            _sample(), [Reference("chr1", 20)], Settings(), store=store,
        )
        rid = report.run_id
    with ProvenanceStore(db) as store2:
        row = store2.get_run(rid)
        assert row["status"] == "completed"


def test_tsv_undetermined_rows_are_persisted(tmp_path):
    from depthcov.engine import analyze_lines

    db = str(tmp_path / "tsv.db")
    lines = [
        "good\tchr1\t0\t60\t\t+\td\t0\t5M",
        "BROKEN",
        "x\tchr1\tBAD\t60\t\t+\td\t0\t1M",
    ]
    with ProvenanceStore(db) as store:
        report = analyze_lines(
            lines, [Reference("chr1", 20)], Settings(),
            store=store, use_external_sort=False,
        )
        rid = report.run_id
        run = store.get_run(rid)
        assert run["input_count"] == 3
        assert run["accepted_count"] == 1
        assert run["undetermined_count"] == 2
        rows = list(store.iter_records(rid))
        reasons = sorted(r["reason"] for r in rows)
        assert reasons == ["accepted", "malformed_record", "malformed_record"]
