"""状态与审计层:SQLite 持久化批次、提交、聚合产物与审计事件。

大整数一律以十进制字符串存储(SQLite 整型只有 64 位)。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id          TEXT PRIMARY KEY,
    label             TEXT,
    n                 TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    sum_bound         TEXT NOT NULL,
    used_bound        TEXT NOT NULL DEFAULT '0',
    max_plaintext_abs TEXT NOT NULL,
    max_weight_abs    TEXT NOT NULL,
    privkey_enc       BLOB NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    sub_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       TEXT NOT NULL REFERENCES batches(batch_id),
    participant_id TEXT NOT NULL,
    c              TEXT NOT NULL,
    exponent       INTEGER NOT NULL,
    weight         TEXT NOT NULL,
    declared_abs   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aggregates (
    agg_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          TEXT NOT NULL REFERENCES batches(batch_id),
    c                 TEXT NOT NULL,
    submission_count  INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    batch_id   TEXT,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- batches ----
    def insert_batch(self, batch: dict) -> None:
        self._conn.execute(
            """INSERT INTO batches
               (batch_id, label, n, fingerprint, sum_bound, used_bound,
                max_plaintext_abs, max_weight_abs, privkey_enc, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (batch["batch_id"], batch.get("label"), batch["n"],
             batch["fingerprint"], batch["sum_bound"], "0",
             batch["max_plaintext_abs"], batch["max_weight_abs"],
             batch["privkey_enc"], _utcnow()),
        )
        self._conn.commit()

    def get_batch(self, batch_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return dict(row) if row else None

    def add_used_bound(self, batch_id: str, delta: int) -> int:
        """累加已占用上界并返回新值。

        上界可超过 64 位,故用 Python 大整数读-改-写,而非 SQL 整型运算。
        并发安全由 service 层的锁保证(本地单进程测试足够)。
        """
        row = self._conn.execute(
            "SELECT used_bound FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        new_value = int(row["used_bound"]) + delta
        self._conn.execute(
            "UPDATE batches SET used_bound = ? WHERE batch_id = ?",
            (str(new_value), batch_id),
        )
        self._conn.commit()
        return new_value

    # ---- submissions ----
    def insert_submission(self, sub: dict) -> int:
        cur = self._conn.execute(
            """INSERT INTO submissions
               (batch_id, participant_id, c, exponent, weight, declared_abs, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (sub["batch_id"], sub["participant_id"], sub["c"], sub["exponent"],
             sub["weight"], sub["declared_abs"], _utcnow()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_submissions(self, batch_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM submissions WHERE batch_id = ? ORDER BY sub_id",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- aggregates ----
    def insert_aggregate(self, batch_id: str, c: str, count: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO aggregates (batch_id, c, submission_count, created_at)"
            " VALUES (?,?,?,?)",
            (batch_id, c, count, _utcnow()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def latest_aggregate(self, batch_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM aggregates WHERE batch_id = ? ORDER BY agg_id DESC LIMIT 1",
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---- audit ----
    def audit(self, run_id: str, batch_id: Optional[str], event: str,
              detail: Any = None) -> None:
        self._conn.execute(
            "INSERT INTO audit (run_id, batch_id, event, detail, created_at)"
            " VALUES (?,?,?,?,?)",
            (run_id, batch_id, event,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             _utcnow()),
        )
        self._conn.commit()

    def list_audit(self, batch_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE batch_id = ? ORDER BY event_id",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]
