"""Result provenance: every run is persisted to SQLite with its config,
input digest, per-record decisions, segments and histogram, so any reported
number can be traced back to the exact input and parameters that made it.

Read names are stored only as salted digests — the audit trail links
decisions to records without keeping potentially identifying sample
metadata at rest.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .models import CoverageResult, Decision, Segment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    ref_name TEXT NOT NULL,
    ref_length INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    counters_json TEXT NOT NULL,
    histogram_json TEXT NOT NULL,
    covered_bases INTEGER NOT NULL,
    weighted_bases INTEGER NOT NULL,
    mean_depth REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    depth INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_run ON segments(run_id);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    record_index INTEGER NOT NULL,
    read_id_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id, status);
"""


def digest_read_id(read_id: str) -> str:
    return hashlib.sha256(read_id.encode("utf-8")).hexdigest()[:16]


class ProvenanceStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_run(
        self,
        run_id: str,
        ref_name: str,
        ref_length: int,
        config: dict,
        input_sha256: str,
        result: CoverageResult,
    ) -> None:
        counters: dict[str, int] = {}
        for decision in result.decisions:
            key = f"{decision.status.value}:{decision.reason.value}"
            counters[key] = counters.get(key, 0) + 1
        mean_depth = result.weighted_bases / ref_length if ref_length else 0.0
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    ref_name,
                    ref_length,
                    json.dumps(config, sort_keys=True),
                    input_sha256,
                    "COMPLETED",
                    json.dumps(counters, sort_keys=True),
                    json.dumps({str(k): v for k, v in sorted(result.histogram.items())}),
                    result.covered_bases,
                    result.weighted_bases,
                    mean_depth,
                ),
            )
            self._conn.executemany(
                "INSERT INTO segments VALUES (?,?,?,?)",
                [(run_id, s.start, s.end, s.depth) for s in result.segments],
            )
            self._conn.executemany(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        d.record_index,
                        digest_read_id(d.read_id),
                        d.status.value,
                        d.reason.value,
                        d.detail,
                    )
                    for d in result.decisions
                ],
            )

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "reference": {"name": row["ref_name"], "length": row["ref_length"]},
            "config": json.loads(row["config_json"]),
            "input_sha256": row["input_sha256"],
            "status": row["status"],
            "counters": json.loads(row["counters_json"]),
            "histogram": {int(k): v for k, v in json.loads(row["histogram_json"]).items()},
            "covered_bases": row["covered_bases"],
            "weighted_bases": row["weighted_bases"],
            "mean_depth": row["mean_depth"],
        }

    def get_segments(self, run_id: str) -> list[Segment]:
        rows = self._conn.execute(
            "SELECT start, end, depth FROM segments WHERE run_id = ? ORDER BY start",
            (run_id,),
        ).fetchall()
        return [Segment(r["start"], r["end"], r["depth"]) for r in rows]

    def get_decisions(self, run_id: str, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE run_id = ? ORDER BY record_index",
                (run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE run_id = ? AND status = ? "
                "ORDER BY record_index",
                (run_id, status),
            ).fetchall()
        return [
            {
                "record_index": r["record_index"],
                "read_id_digest": r["read_id_digest"],
                "status": r["status"],
                "reason": r["reason"],
                "detail": r["detail"],
            }
            for r in rows
        ]
