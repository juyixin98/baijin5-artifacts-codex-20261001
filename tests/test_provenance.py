"""Provenance store tests: roundtrip, conflict, diagnostics content."""
import sqlite3

import pytest

from seqdist.errors import ErrorCategory, StateConflictError
from seqdist.provenance import RunRecord, connect, get_run, record_run
from seqdist.service import run_distance


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "runs.sqlite3"))
    yield c
    c.close()


def make_record(run_id: str = "r1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        created_at="2026-10-04T00:00:00+00:00",
        model="jc69",
        seed=7,
        n_replicates=100,
        alpha=0.05,
        input_sha256="ab" * 32,
        status="ok",
        result={"distance": 0.1},
        intermediates={"n_valid": 20, "p": 0.1},
    )


def test_record_and_get_roundtrip(conn):
    record_run(conn, make_record())
    got = get_run(conn, "r1")
    assert got is not None
    assert got["run_id"] == "r1"
    assert got["seed"] == 7
    assert got["result"] == {"distance": 0.1}
    assert got["intermediates"] == {"n_valid": 20, "p": 0.1}


def test_duplicate_run_id_is_state_conflict(conn):
    record_run(conn, make_record())
    with pytest.raises(StateConflictError) as excinfo:
        record_run(conn, make_record())
    assert excinfo.value.category is ErrorCategory.STATE_CONFLICT
    assert excinfo.value.detail["run_id"] == "r1"


def test_get_missing_run_returns_none(conn):
    assert get_run(conn, "nope") is None


def test_run_distance_records_replayable_provenance(conn):
    result = run_distance(
        conn,
        seq1="ACGTACGTACGTACGTACGT",
        seq2="ACGTACGTACGTACGAACGT",
        model="jc69",
        n_replicates=100,
        alpha=0.05,
        seed=9,
    )
    record = get_run(conn, result["run_id"])
    assert record is not None
    # key intermediate state needed to replay the judgement
    inter = record["intermediates"]
    assert inter["n_valid"] == 20
    assert inter["n_match"] == 19
    assert inter["p"] == pytest.approx(1 / 20)
    assert inter["estimate_status"] == "ok"
    assert inter["bootstrap_n_ok"] + inter["bootstrap_n_saturated"] == 100
    # stored result equals returned result; run id links them
    assert record["result"] == result
    assert record["seed"] == 9
    assert len(record["input_sha256"]) == 64


def test_run_distance_logs_run_id_and_intermediates(conn, caplog):
    with caplog.at_level("INFO", logger="seqdist.service"):
        result = run_distance(
            conn, seq1="AAAA", seq2="AAAG", model="p",
            n_replicates=10, alpha=0.05, seed=1,
        )
    run_records = [r for r in caplog.records if getattr(r, "run_id", None) == result["run_id"]]
    assert run_records, "expected log records tagged with the run id"
    text = " ".join(r.getMessage() for r in run_records)
    assert "n_valid=4" in text
    assert "status=ok" in text
