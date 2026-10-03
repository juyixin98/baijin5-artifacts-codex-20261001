"""SQLite-backed provenance store.

Every scan request — successful or failed — is persisted with its request
identity, the configuration and versions that produced it, a hash of the
canonical request, and (on success) every hit. This is what makes results
traceable after the fact: a request_id is enough to reconstruct what was
asked, what ran, and what came out.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    request_id        TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    app_version       TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    request_hash      TEXT NOT NULL,
    status            TEXT NOT NULL,          -- 'ok' | 'failed'
    params_json       TEXT NOT NULL,
    config_json       TEXT NOT NULL,
    summary_json      TEXT NOT NULL           -- result summary or error object
);
CREATE TABLE IF NOT EXISTS hits (
    request_id         TEXT NOT NULL REFERENCES scans(request_id),
    hit_index          INTEGER NOT NULL,
    seq_id             TEXT NOT NULL,
    strand             TEXT NOT NULL,
    start              INTEGER NOT NULL,
    end                INTEGER NOT NULL,
    matched            TEXT NOT NULL,
    score              REAL NOT NULL,
    pvalue             REAL NOT NULL,
    bonferroni         REAL NOT NULL,
    benjamini_hochberg REAL NOT NULL,
    PRIMARY KEY (request_id, hit_index)
);
"""


class ProvenanceStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # An in-memory database only survives within one connection, so keep
        # a single shared connection for it; file databases connect per call.
        self._mem_conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def save_scan(
        self,
        *,
        request_id: str,
        app_version: str,
        algorithm_version: str,
        request_hash: str,
        status: str,
        params: dict,
        config: dict,
        summary: dict,
        hits: list[dict],
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scans (request_id, created_at, app_version, algorithm_version,
                                   request_hash, status, params_json, config_json, summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    created_at,
                    app_version,
                    algorithm_version,
                    request_hash,
                    status,
                    json.dumps(params, sort_keys=True),
                    json.dumps(config, sort_keys=True),
                    json.dumps(summary, sort_keys=True),
                ),
            )
            conn.executemany(
                """
                INSERT INTO hits (request_id, hit_index, seq_id, strand, start, end,
                                  matched, score, pvalue, bonferroni, benjamini_hochberg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        request_id,
                        i,
                        h["seq_id"],
                        h["strand"],
                        h["start"],
                        h["end"],
                        h["matched"],
                        h["score"],
                        h["pvalue"],
                        h["bonferroni"],
                        h["benjamini_hochberg"],
                    )
                    for i, h in enumerate(hits)
                ],
            )

    def get_scan(self, request_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM scans WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                return None
            hit_rows = conn.execute(
                "SELECT * FROM hits WHERE request_id = ? ORDER BY hit_index", (request_id,)
            ).fetchall()
        return {
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "app_version": row["app_version"],
            "algorithm_version": row["algorithm_version"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "params": json.loads(row["params_json"]),
            "config": json.loads(row["config_json"]),
            "summary": json.loads(row["summary_json"]),
            "hits": [
                {
                    "seq_id": h["seq_id"],
                    "strand": h["strand"],
                    "start": h["start"],
                    "end": h["end"],
                    "matched": h["matched"],
                    "score": h["score"],
                    "pvalue": h["pvalue"],
                    "bonferroni": h["bonferroni"],
                    "benjamini_hochberg": h["benjamini_hochberg"],
                }
                for h in hit_rows
            ],
        }
