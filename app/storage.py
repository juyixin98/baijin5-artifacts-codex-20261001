"""SQLite persistence for requests, results and evidence.

The database is local and stores only synthetic data.  Each request row is
keyed by its ``request_id`` so an API response can be traced back to exactly
what was computed, with which service version and which computation kind.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL UNIQUE,
    endpoint        TEXT NOT NULL,
    method          TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    result_json     TEXT,
    status          TEXT NOT NULL,
    computation     TEXT,
    certified       INTEGER,
    service_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    code        TEXT,
    message     TEXT NOT NULL,
    detail_json TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(request_id)
);
"""


class Storage:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record_request(
        self,
        request_id: str,
        endpoint: str,
        method: str,
        payload: Dict[str, Any],
        envelope: Dict[str, Any],
        computation: Optional[str],
        certified: Optional[bool],
    ) -> None:
        result = envelope.get("result")
        status = envelope.get("status", "error")
        self._conn.execute(
            """
            INSERT OR REPLACE INTO requests
                (request_id, endpoint, method, payload_json, result_json, status,
                 computation, certified, service_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                endpoint,
                method,
                json.dumps(payload, sort_keys=True),
                json.dumps(result, sort_keys=True) if result is not None else None,
                status,
                computation,
                None if certified is None else int(certified),
                __version__,
            ),
        )
        for step in envelope.get("steps", []):
            self._conn.execute(
                "INSERT INTO evidence (request_id, seq, kind, message, detail_json) "
                "VALUES (?, ?, 'step', ?, ?)",
                (request_id, step["seq"], step["summary"], json.dumps(step, sort_keys=True)),
            )
        for i, failure in enumerate(envelope.get("failures", []), start=1):
            self._conn.execute(
                "INSERT INTO evidence (request_id, seq, kind, code, message, detail_json) "
                "VALUES (?, ?, 'failure', ?, ?, ?)",
                (
                    request_id,
                    len(envelope.get("steps", [])) + i,
                    failure["code"],
                    failure["message"],
                    json.dumps(failure.get("detail", {}), sort_keys=True),
                ),
            )
        for i, text in enumerate(envelope.get("uncertainties", []), start=1):
            self._conn.execute(
                "INSERT INTO evidence (request_id, seq, kind, message) VALUES (?, ?, 'uncertainty', ?)",
                (request_id, len(envelope.get("steps", [])) + len(envelope.get("failures", [])) + i, text),
            )
        self._conn.commit()

    def fetch_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        out: Dict[str, Any] = dict(row)
        for key in ("payload_json", "result_json"):
            out[key] = json.loads(out[key]) if out.get(key) else None
        return out

    def list_requests(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT request_id, endpoint, method, status, computation, certified, created_at "
            "FROM requests ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
