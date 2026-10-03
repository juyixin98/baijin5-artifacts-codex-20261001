"""SQLite-backed provenance: every reported number is traceable to inputs.

Schema (versioned) stores, per run:

* each input record's coordinates/CIGAR and its accept/reject verdict,
* each reference's aggregate result and histogram,
* every constant-depth segment.

A verifier can re-read the stored rows and recompute totals independently.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dedup_policy TEXT NOT NULL,
    min_mapq INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    undetermined_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    record_index INTEGER NOT NULL,
    qname TEXT NOT NULL,
    ref TEXT,
    ref_start INTEGER,
    ref_end INTEGER,
    mapq INTEGER,
    cigar TEXT,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL,
    covered_bases INTEGER NOT NULL,
    PRIMARY KEY (run_id, record_index)
);
CREATE TABLE IF NOT EXISTS ref_results (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    ref_name TEXT NOT NULL,
    ref_length INTEGER NOT NULL,
    weighted_length INTEGER NOT NULL,
    covered_bases INTEGER NOT NULL,
    PRIMARY KEY (run_id, ref_name)
);
CREATE TABLE IF NOT EXISTS histograms (
    run_id TEXT NOT NULL,
    ref_name TEXT NOT NULL,
    depth INTEGER NOT NULL,
    bases INTEGER NOT NULL,
    PRIMARY KEY (run_id, ref_name, depth)
);
CREATE TABLE IF NOT EXISTS segments (
    run_id TEXT NOT NULL,
    ref_name TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    depth INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_run_ref
    ON segments(run_id, ref_name, start);
"""


class ProvenanceStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        # FastAPI runs sync endpoints in a worker threadpool, so the single
        # connection is shared across threads and writes are lock-serialized.
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProvenanceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def save_run(self, run: "RunRecord") -> None:
        with self._lock, self.conn:
            self._save_run_locked(run)

    def _save_run_locked(self, run: "RunRecord") -> None:
        self.conn.execute(
            """INSERT INTO runs(run_id, request_id, created_at, dedup_policy,
               min_mapq, status, input_count, accepted_count,
               rejected_count, undetermined_count)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run.run_id, run.request_id, run.created_at, run.dedup_policy,
                run.min_mapq, run.status, run.input_count,
                run.accepted_count, run.rejected_count,
                run.undetermined_count,
            ),
        )
        self.conn.executemany(
            """INSERT INTO records(run_id, record_index, qname, ref, ref_start,
               ref_end, mapq, cigar, accepted, reason, detail, covered_bases)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    run.run_id, i, v.query_name, v.ref_name, v.ref_start,
                    v.ref_end, v.mapq,
                    run.raw_cigars.get(i),
                    1 if v.accepted else 0, v.reason, v.detail,
                    sum(b.length for b in v.blocks),
                )
                for i, v in enumerate(run.verdicts)
            ],
        )
        for result in run.results:
            self.conn.execute(
                """INSERT INTO ref_results(run_id, ref_name, ref_length,
                   weighted_length, covered_bases) VALUES (?,?,?,?,?)""",
                (run.run_id, result.ref_name, result.ref_length,
                 result.weighted_length, result.covered_bases),
            )
            self.conn.executemany(
                """INSERT INTO histograms(run_id, ref_name, depth, bases)
                   VALUES (?,?,?,?)""",
                [
                    (run.run_id, result.ref_name, int(d), int(c))
                    for d, c in sorted(result.histogram.items())
                ],
            )
            self.conn.executemany(
                """INSERT INTO segments(run_id, ref_name, start, end, depth)
                   VALUES (?,?,?,?,?)""",
                [
                    (run.run_id, result.ref_name, s.start, s.end, s.depth)
                    for s in result.segments
                ],
            )
        self.conn.commit()

    # ---- read-back for verification ----

    def append_undetermined(
        self, run_id: str, verdicts: list[Verdict], start_index: int
    ) -> None:
        """Persist malformed/undetermined rows found outside typed decoding."""
        with self._lock, self.conn:
            self.conn.executemany(
                """INSERT INTO records(run_id, record_index, qname, ref,
                   ref_start, ref_end, mapq, cigar, accepted, reason,
                   detail, covered_bases)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, start_index + offset, v.query_name,
                        v.ref_name, v.ref_start, v.ref_end, v.mapq, None,
                        0, v.reason, v.detail, 0,
                    )
                    for offset, v in enumerate(verdicts)
                ],
            )
            self.conn.execute(
                "UPDATE runs SET input_count = input_count + ?, "
                "undetermined_count = undetermined_count + ? WHERE run_id = ?",
                (len(verdicts), len(verdicts), run_id),
            )

    def get_run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown run_id {run_id!r}")
        return row

    def iter_records(self, run_id: str) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute(
            "SELECT * FROM records WHERE run_id = ? ORDER BY record_index",
            (run_id,),
        )

    def get_segments(self, run_id: str, ref_name: str):
        return self.conn.execute(
            "SELECT start,end,depth FROM segments WHERE run_id=? AND ref_name=? "
            "ORDER BY start",
            (run_id, ref_name),
        ).fetchall()

    def get_histogram(self, run_id: str, ref_name: str) -> dict[int, int]:
        rows = self.conn.execute(
            "SELECT depth,bases FROM histograms WHERE run_id=? AND ref_name=?",
            (run_id, ref_name),
        ).fetchall()
        return {int(r["depth"]): int(r["bases"]) for r in rows}

    def get_ref_result(self, run_id: str, ref_name: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM ref_results WHERE run_id=? AND ref_name=?",
            (run_id, ref_name),
        ).fetchone()
        if row is None:
            raise KeyError(f"no result for {run_id!r}/{ref_name!r}")
        return row


@contextmanager
def open_store(db_path: str) -> Iterator[ProvenanceStore]:
    store = ProvenanceStore(db_path)
    try:
        yield store
    finally:
        store.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Late imports kept at module bottom to avoid cycles at class definition time.
from .models import DepthResult, Verdict  # noqa: E402


class RunRecord:
    """Plain container describing one persisted run."""

    def __init__(
        self,
        *,
        run_id: str,
        request_id: str,
        created_at: str,
        dedup_policy: str,
        min_mapq: int,
        status: str,
        input_count: int,
        verdicts: list[Verdict],
        results: list[DepthResult],
        raw_cigars: dict[int, str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.request_id = request_id
        self.created_at = created_at
        self.dedup_policy = dedup_policy
        self.min_mapq = min_mapq
        self.status = status
        self.input_count = input_count
        self.verdicts = verdicts
        self.results = results
        self.raw_cigars = raw_cigars or {}

    @property
    def accepted_count(self) -> int:
        return sum(1 for v in self.verdicts if v.accepted)

    @property
    def rejected_count(self) -> int:
        return sum(
            1 for v in self.verdicts
            if not v.accepted and v.reason != "undetermined"
        )

    @property
    def undetermined_count(self) -> int:
        return sum(1 for v in self.verdicts if v.reason == "undetermined")


def dump_run_json(run: "RunRecord") -> str:
    """Serialize run metadata (no arrays) for diagnostics/checkpoints."""
    return json.dumps(
        {
            "run_id": run.run_id,
            "request_id": run.request_id,
            "created_at": run.created_at,
            "dedup_policy": run.dedup_policy,
            "min_mapq": run.min_mapq,
            "status": run.status,
            "counts": {
                "input": run.input_count,
                "accepted": run.accepted_count,
                "rejected": run.rejected_count,
                "undetermined": run.undetermined_count,
            },
        },
        indent=2,
    )
