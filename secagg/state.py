"""状态与审计:SQLite 持久化运行状态、消息与审计日志.

设计要点:
- 每次聚合运行有唯一 run_id,所有状态与审计条目都挂在 run_id 下,
  测试日志可凭 run_id 重放问题。
- 审计日志为追加式(append-only),记录阶段、事件、判断理由,
  例如"拒绝重复恢复消息:内容不同 -> state_conflict"。
- 所有写入在事务内完成;阶段推进由 protocol.py 驱动,本模块不做协议判断。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    config      TEXT NOT NULL,
    phase       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    abort_reason TEXT
);
CREATE TABLE IF NOT EXISTS clients (
    run_id      TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    c_pk        BLOB,
    s_pk        BLOB,
    keys_at     REAL,
    PRIMARY KEY (run_id, client_id)
);
CREATE TABLE IF NOT EXISTS shares (
    run_id      TEXT NOT NULL,
    sender      TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    ciphertext  BLOB NOT NULL,
    PRIMARY KEY (run_id, sender, recipient)
);
CREATE TABLE IF NOT EXISTS masked_inputs (
    run_id      TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    vector      TEXT NOT NULL,
    PRIMARY KEY (run_id, client_id)
);
CREATE TABLE IF NOT EXISTS active_set (
    run_id      TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    PRIMARY KEY (run_id, client_id)
);
CREATE TABLE IF NOT EXISTS recovery (
    run_id      TEXT NOT NULL,
    sender      TEXT NOT NULL,
    b_shares    TEXT NOT NULL,
    sk_shares   TEXT NOT NULL,
    PRIMARY KEY (run_id, sender)
);
CREATE TABLE IF NOT EXISTS results (
    run_id      TEXT PRIMARY KEY,
    vector      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    ts          REAL NOT NULL,
    phase       TEXT NOT NULL,
    event       TEXT NOT NULL,
    rationale   TEXT NOT NULL
);
"""


class StateStore:
    """单文件 SQLite 存储;线程安全(服务器单进程多线程下串行化写)。"""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 运行 ----

    def create_run(self, config: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, created_at, config, phase, status) "
                "VALUES (?,?,?,?,?)",
                (run_id, time.time(), json.dumps(config), "KEYS", "ACTIVE"),
            )
        self.audit(run_id, "KEYS", "run_created", f"配置: {json.dumps(config, sort_keys=True)}")
        return run_id

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()

    def set_phase(self, run_id: str, phase: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET phase = ? WHERE run_id = ?", (phase, run_id)
            )

    def set_aborted(self, run_id: str, reason: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status = 'ABORTED', abort_reason = ? WHERE run_id = ?",
                (reason, run_id),
            )

    def set_done(self, run_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status = 'DONE' WHERE run_id = ?", (run_id,)
            )

    # ---- 客户端与消息 ----

    def add_client_keys(self, run_id: str, client_id: str, c_pk: bytes, s_pk: bytes) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO clients (run_id, client_id, c_pk, s_pk, keys_at) "
                "VALUES (?,?,?,?,?)",
                (run_id, client_id, c_pk, s_pk, time.time()),
            )

    def get_clients(self, run_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM clients WHERE run_id = ? ORDER BY client_id", (run_id,)
            ).fetchall()

    def put_share(self, run_id: str, sender: str, recipient: str, ciphertext: bytes) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO shares (run_id, sender, recipient, ciphertext) "
                "VALUES (?,?,?,?)",
                (run_id, sender, recipient, ciphertext),
            )

    def get_shares_for(self, run_id: str, recipient: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT sender, ciphertext FROM shares WHERE run_id = ? AND recipient = ?",
                (run_id, recipient),
            ).fetchall()

    def count_senders(self, run_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT sender) AS n FROM shares WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return int(row["n"])

    def has_shares_from(self, run_id: str, sender: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM shares WHERE run_id = ? AND sender = ? LIMIT 1",
                (run_id, sender),
            ).fetchone()
            return row is not None

    def get_share_senders(self, run_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT sender FROM shares WHERE run_id = ? ORDER BY sender",
                (run_id,),
            ).fetchall()
            return [r["sender"] for r in rows]

    def put_result(self, run_id: str, vector: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO results (run_id, vector) VALUES (?,?)",
                (run_id, json.dumps(vector)),
            )

    def get_result(self, run_id: str) -> list[int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT vector FROM results WHERE run_id = ?", (run_id,)
            ).fetchone()
            return json.loads(row["vector"]) if row else None

    def put_masked_input(self, run_id: str, client_id: str, vector: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO masked_inputs (run_id, client_id, vector) VALUES (?,?,?)",
                (run_id, client_id, json.dumps(vector)),
            )

    def get_masked_inputs(self, run_id: str) -> dict[str, list[int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_id, vector FROM masked_inputs WHERE run_id = ?", (run_id,)
            ).fetchall()
            return {r["client_id"]: json.loads(r["vector"]) for r in rows}

    def freeze_active_set(self, run_id: str, client_ids: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO active_set (run_id, client_id) VALUES (?,?)",
                [(run_id, c) for c in client_ids],
            )

    def get_active_set(self, run_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_id FROM active_set WHERE run_id = ? ORDER BY client_id",
                (run_id,),
            ).fetchall()
            return [r["client_id"] for r in rows]

    def put_recovery(
        self, run_id: str, sender: str, b_shares: dict, sk_shares: dict
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO recovery (run_id, sender, b_shares, sk_shares) "
                "VALUES (?,?,?,?)",
                (run_id, sender, json.dumps(b_shares), json.dumps(sk_shares)),
            )

    def get_recovery(self, run_id: str, sender: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM recovery WHERE run_id = ? AND sender = ?",
                (run_id, sender),
            ).fetchone()

    def get_all_recovery(self, run_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM recovery WHERE run_id = ?", (run_id,)
            ).fetchall()

    # ---- 审计 ----

    def audit(self, run_id: str, phase: str, event: str, rationale: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (run_id, ts, phase, event, rationale) "
                "VALUES (?,?,?,?,?)",
                (run_id, time.time(), phase, event, rationale),
            )

    def get_audit(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]
