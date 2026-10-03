"""SQLite-backed provenance store.

Every scan/calibrate request — completed or failed — is recorded with its
request id, resolved configuration, input hash, app version and outcome, so
any result can be traced back to exactly what was computed and why.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id   TEXT PRIMARY KEY,
    endpoint     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    app_version  TEXT NOT NULL,
    status       TEXT NOT NULL,          -- 'completed' | 'failed'
    config_json  TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    result_json  TEXT,
    error_json   TEXT
);
"""


class ProvenanceStore:
    def __init__(self, path: str):
        self.path = path
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        *,
        request_id: str,
        endpoint: str,
        app_version: str,
        status: str,
        config: dict,
        input_sha256: str,
        summary: dict,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO requests
                (request_id, endpoint, created_at, app_version, status,
                 config_json, input_sha256, summary_json, result_json, error_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    endpoint,
                    datetime.now(timezone.utc).isoformat(),
                    app_version,
                    status,
                    json.dumps(config, sort_keys=True),
                    input_sha256,
                    json.dumps(summary, sort_keys=True),
                    json.dumps(result) if result is not None else None,
                    json.dumps(error) if error is not None else None,
                ),
            )

    def get(self, request_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        for field in ("config_json", "summary_json", "result_json", "error_json"):
            value = record.pop(field)
            record[field.removesuffix("_json")] = json.loads(value) if value else None
        return record
