"""SQLite persistence for datasets and resumable mining jobs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .corpus import Transaction
from .miner import Itemset, StackFrame

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id            TEXT PRIMARY KEY,
    name                  TEXT,
    content_hash          TEXT NOT NULL,
    engine_version        TEXT NOT NULL,
    transaction_count     INTEGER NOT NULL,
    distinct_item_count   INTEGER NOT NULL,
    empty_transaction_count INTEGER NOT NULL,
    duplicate_transaction_count INTEGER NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    tid        TEXT NOT NULL,
    items_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, position)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    request_id       TEXT NOT NULL,
    dataset_id       TEXT NOT NULL REFERENCES datasets(dataset_id),
    dataset_hash     TEXT NOT NULL,
    engine_version   TEXT NOT NULL,
    min_support      INTEGER NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('running','complete')),
    complete         INTEGER NOT NULL,
    evaluations_used INTEGER NOT NULL,
    frames_json      TEXT NOT NULL,
    closed_json      TEXT NOT NULL,
    notes_json       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    name: str | None
    content_hash: str
    engine_version: str
    transaction_count: int
    distinct_item_count: int
    empty_transaction_count: int
    duplicate_transaction_count: int


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    request_id: str
    dataset_id: str
    dataset_hash: str
    engine_version: str
    min_support: int
    status: str
    complete: bool
    evaluations_used: int
    frames: list[StackFrame]
    closed: dict[Itemset, int]
    notes: list[str]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- datasets -----------------------------------------------------------

    def insert_dataset(
        self,
        dataset_id: str,
        name: str | None,
        content_hash: str,
        engine_version: str,
        transactions: list[Transaction],
        stats: dict,
    ) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    dataset_id,
                    name,
                    content_hash,
                    engine_version,
                    stats["transaction_count"],
                    stats["distinct_item_count"],
                    stats["empty_transaction_count"],
                    stats["duplicate_transaction_count"],
                    now,
                ),
            )
            conn.executemany(
                "INSERT INTO transactions (dataset_id, position, tid, items_json) VALUES (?,?,?,?)",
                [
                    (dataset_id, pos, txn.tid, json.dumps(list(txn.items), ensure_ascii=False))
                    for pos, txn in enumerate(transactions)
                ],
            )

    def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    def load_transactions(self, dataset_id: str) -> list[Transaction]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tid, items_json FROM transactions WHERE dataset_id = ? ORDER BY position",
                (dataset_id,),
            ).fetchall()
        return [Transaction(tid=r["tid"], items=tuple(json.loads(r["items_json"]))) for r in rows]

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> DatasetRecord:
        return DatasetRecord(
            dataset_id=row["dataset_id"],
            name=row["name"],
            content_hash=row["content_hash"],
            engine_version=row["engine_version"],
            transaction_count=row["transaction_count"],
            distinct_item_count=row["distinct_item_count"],
            empty_transaction_count=row["empty_transaction_count"],
            duplicate_transaction_count=row["duplicate_transaction_count"],
        )

    # -- jobs ---------------------------------------------------------------

    def upsert_job(self, record: JobRecord) -> None:
        now = _utcnow()
        frames_json = json.dumps([f.to_jsonable() for f in record.frames])
        closed_json = json.dumps(
            sorted(([sorted(x), s] for x, s in record.closed.items()), key=lambda e: e[0]),
            ensure_ascii=False,
        )
        notes_json = json.dumps(record.notes, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, request_id, dataset_id, dataset_hash, engine_version,
                                  min_support, status, complete, evaluations_used,
                                  frames_json, closed_json, notes_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status, complete=excluded.complete,
                    evaluations_used=excluded.evaluations_used,
                    frames_json=excluded.frames_json, closed_json=excluded.closed_json,
                    notes_json=excluded.notes_json, updated_at=excluded.updated_at
                """,
                (
                    record.job_id,
                    record.request_id,
                    record.dataset_id,
                    record.dataset_hash,
                    record.engine_version,
                    record.min_support,
                    record.status,
                    1 if record.complete else 0,
                    record.evaluations_used,
                    frames_json,
                    closed_json,
                    notes_json,
                    now,
                    now,
                ),
            )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        frames = [StackFrame.from_jsonable(d) for d in json.loads(row["frames_json"])]
        closed = {
            frozenset(items): support for items, support in json.loads(row["closed_json"])
        }
        return JobRecord(
            job_id=row["job_id"],
            request_id=row["request_id"],
            dataset_id=row["dataset_id"],
            dataset_hash=row["dataset_hash"],
            engine_version=row["engine_version"],
            min_support=row["min_support"],
            status=row["status"],
            complete=bool(row["complete"]),
            evaluations_used=row["evaluations_used"],
            frames=frames,
            closed=closed,
            notes=json.loads(row["notes_json"]),
        )
