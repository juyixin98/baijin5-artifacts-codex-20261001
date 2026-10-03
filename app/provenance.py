"""SQLite-backed provenance store.

Every phasing request — successful or failed — is recorded with its
request id, input hash, software/algorithm versions, and the full result
or the categorized error, so any answer can be traced back to the exact
input and code that produced it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import ALGORITHM_VERSION, __version__
from .config import REPO_ROOT

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    request_id       TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    sample_id        TEXT NOT NULL,
    input_sha256     TEXT NOT NULL,
    app_version      TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    numpy_version    TEXT NOT NULL,
    status           TEXT NOT NULL,           -- 'success' | 'failed'
    failure_category TEXT,                    -- FailureCategory value when failed
    result_json      TEXT,
    error_json       TEXT
);
"""


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


class ProvenanceStore:
    def __init__(self, db_path: str):
        self._path = _resolve_path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record_run(
        self,
        *,
        request_id: str,
        sample_id: str,
        input_sha256: str,
        status: str,
        failure_category: str | None = None,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        row = (
            request_id,
            datetime.now(timezone.utc).isoformat(),
            sample_id,
            input_sha256,
            __version__,
            ALGORITHM_VERSION,
            np.__version__,
            status,
            failure_category,
            json.dumps(result, sort_keys=True) if result is not None else None,
            json.dumps(error, sort_keys=True) if error is not None else None,
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
            )

    def get_run(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("result_json", "error_json"):
            if record[key] is not None:
                record[key] = json.loads(record[key])
        return record
