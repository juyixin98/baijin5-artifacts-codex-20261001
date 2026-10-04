"""状态层：SQLite 持久化。

表结构：
- records        记录主表，敏感字段只存随机密文信封（BLOB），NULL 表示无该字段
- blind_indexes  盲索引表，按 (字段, 索引密钥版本) 组织，支持多版本并存
- meta           轮换状态机（query 版本集、轮换阶段）
- audit          审计日志（仅记录身份与安全元数据，见 audit.py 的键白名单）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .protocol import FIELDS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id   TEXT PRIMARY KEY,
    enc_version INTEGER NOT NULL,
    email_ct    BLOB,
    phone_ct    BLOB,
    name_ct     BLOB,
    id_number_ct BLOB,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blind_indexes (
    field         TEXT NOT NULL,
    index_version INTEGER NOT NULL,
    index_hex     TEXT NOT NULL,
    record_id     TEXT NOT NULL,
    PRIMARY KEY (field, index_version, index_hex, record_id)
);
CREATE INDEX IF NOT EXISTS idx_blind_lookup
    ON blind_indexes (field, index_hex);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    request_id TEXT NOT NULL,
    op         TEXT NOT NULL,
    record_id  TEXT,
    detail     TEXT NOT NULL
);
"""

_CT_COLUMNS = {f: f"{f}_ct" for f in FIELDS}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: str | Path):
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 记录 -------------------------------------------------------------
    def insert_record(self, record_id: str, enc_version: int, cts: dict[str, bytes | None]) -> None:
        cols = ["record_id", "enc_version"] + [_CT_COLUMNS[f] for f in FIELDS] + ["created_at"]
        vals = [record_id, enc_version] + [cts.get(f) for f in FIELDS] + [_utcnow()]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                vals,
            )
            self._conn.commit()

    def get_record(self, record_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_id, enc_version, email_ct, phone_ct, name_ct, id_number_ct, created_at "
                "FROM records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        rec = {"record_id": row[0], "enc_version": row[1], "created_at": row[6]}
        for i, f in enumerate(FIELDS):
            rec[f] = row[2 + i]
        return rec

    def all_record_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT record_id FROM records ORDER BY record_id").fetchall()
        return [r[0] for r in rows]

    # ---- 盲索引 -------------------------------------------------------------
    def insert_index(self, field: str, index_version: int, index_hex: str, record_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO blind_indexes (field, index_version, index_hex, record_id)"
                " VALUES (?,?,?,?)",
                (field, index_version, index_hex, record_id),
            )
            self._conn.commit()

    def find_candidates(self, field: str, index_hexes: list[str]) -> list[str]:
        """按索引值找候选记录（可能含碰撞，必须解密二次确认）。"""
        if not index_hexes:
            return []
        marks = ",".join("?" * len(index_hexes))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT DISTINCT record_id FROM blind_indexes WHERE field = ? AND index_hex IN ({marks})",
                [field, *index_hexes],
            ).fetchall()
        return sorted(r[0] for r in rows)

    def index_versions_present(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT index_version FROM blind_indexes").fetchall()
        return {r[0] for r in rows}

    def has_index(self, field: str, index_version: int, record_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM blind_indexes WHERE field = ? AND index_version = ? AND record_id = ? LIMIT 1",
                (field, index_version, record_id),
            ).fetchone()
        return row is not None

    def delete_indexes_for_versions(self, versions: list[int]) -> int:
        if not versions:
            return 0
        marks = ",".join("?" * len(versions))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM blind_indexes WHERE index_version IN ({marks})", list(versions)
            )
            self._conn.commit()
            return cur.rowcount

    def iter_records(self) -> list[dict]:
        """返回全部记录（本地规模；用于重建索引的批处理）。"""
        return [self.get_record(rid) for rid in self.all_record_ids()]

    # ---- 轮换状态机（meta） -------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_query_versions(self) -> list[int] | None:
        raw = self.get_meta("index_query_versions")
        return json.loads(raw) if raw is not None else None

    def set_query_versions(self, versions: list[int]) -> None:
        self.set_meta("index_query_versions", json.dumps(sorted(set(versions))))

    def get_rotation_state(self) -> str:
        return self.get_meta("rotation_state") or "idle"

    def set_rotation_state(self, state: str) -> None:
        self.set_meta("rotation_state", state)

    # ---- 审计 ---------------------------------------------------------------
    def insert_audit(self, request_id: str, op: str, record_id: str | None, detail: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit (ts, request_id, op, record_id, detail) VALUES (?,?,?,?,?)",
                (_utcnow(), request_id, op, record_id, json.dumps(detail, sort_keys=True)),
            )
            self._conn.commit()

    def list_audit(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, request_id, op, record_id, detail FROM audit ORDER BY id"
            ).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "request_id": r[2],
                "op": r[3],
                "record_id": r[4],
                "detail": json.loads(r[5]),
            }
            for r in rows
        ]
