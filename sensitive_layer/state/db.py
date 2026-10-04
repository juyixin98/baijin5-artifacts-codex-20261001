"""SQLite connection and schema.

A single ``Database`` owns the connection plus a re-entrant lock. FastAPI runs
sync endpoints in a threadpool, so every access goes through the lock and the
connection is created with ``check_same_thread=False``.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records(
  record_id TEXT NOT NULL,
  field TEXT NOT NULL,
  purpose TEXT NOT NULL,
  ciphertext BLOB,              -- NULL when the stored value is NULL
  enc_key_version INTEGER,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(record_id, field, purpose)
);
CREATE TABLE IF NOT EXISTS blind_indexes(
  record_id TEXT NOT NULL,
  field TEXT NOT NULL,
  purpose TEXT NOT NULL,
  index_key_version INTEGER NOT NULL,
  index_value BLOB NOT NULL,
  PRIMARY KEY(record_id, field, purpose, index_key_version)
);
CREATE INDEX IF NOT EXISTS idx_blind_lookup
  ON blind_indexes(field, purpose, index_key_version, index_value);
CREATE TABLE IF NOT EXISTS keys(
  kind TEXT NOT NULL,
  version INTEGER NOT NULL,
  key_bytes BLOB NOT NULL,
  PRIMARY KEY(kind, version)
);
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
