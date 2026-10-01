"""SQLite persistence for RD runs.

Schema (versioned via PRAGMA user_version)::

    runs(run_id TEXT PRIMARY KEY, created_at TEXT, status TEXT,
         failure_category TEXT, request_json TEXT, result_json TEXT)

The full request and result documents are stored verbatim, so a run can be
audited and re-created from the database alone.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..config import settings

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    status            TEXT NOT NULL,
    failure_category  TEXT,
    kernel            TEXT NOT NULL,
    bandwidth_method  TEXT NOT NULL,
    n                 INTEGER NOT NULL,
    request_json      TEXT NOT NULL,
    result_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
"""


class RunStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_DDL)
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def save_run(
        self, request: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        sample = result.get("diagnostics", {}).get("sample", {})
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, created_at, status, failure_category, kernel,
                    bandwidth_method, n, request_json, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["run_id"],
                    datetime.now(timezone.utc).isoformat(),
                    result["status"],
                    result.get("failure_category"),
                    result.get("kernel"),
                    result.get("bandwidth_method"),
                    int(sample.get("n", -1)),
                    json.dumps(request, sort_keys=True, ensure_ascii=False),
                    json.dumps(result, sort_keys=True, ensure_ascii=False),
                ),
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT run_id, created_at, status, failure_category,
                          kernel, bandwidth_method, n
                   FROM runs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
