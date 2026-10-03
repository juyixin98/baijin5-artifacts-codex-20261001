"""SQLite-backed provenance store.

Every phasing request — successful, ambiguous, or failed — is recorded with
its request id, input hash, versions, status, and full result payload, so
any reported haplotype can be traced back to exactly what was computed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    request_id       TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    app_version      TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_json      TEXT NOT NULL,
    input_sha256     TEXT NOT NULL,
    status           TEXT NOT NULL,
    failure_category TEXT,
    failure_detail   TEXT,
    result_json      TEXT NOT NULL
)
"""


class ProvenanceStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_run(
        self,
        *,
        request_id: str,
        app_version: str,
        algorithm_version: str,
        config: dict,
        input_sha256: str,
        status: str,
        result: dict,
        failure_category: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    request_id, created_at, app_version, algorithm_version,
                    config_json, input_sha256, status,
                    failure_category, failure_detail, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    datetime.now(timezone.utc).isoformat(),
                    app_version,
                    algorithm_version,
                    json.dumps(config, sort_keys=True),
                    input_sha256,
                    status,
                    failure_category,
                    failure_detail,
                    json.dumps(result, ensure_ascii=False),
                ),
            )

    def get_run(self, request_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["config"] = json.loads(record.pop("config_json"))
        record["result"] = json.loads(record.pop("result_json"))
        return record
