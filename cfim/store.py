"""SQLite persistence and the vertical tid index.

Schema
------
* ``corpora``            - one row per uploaded normalized corpus.
* ``transactions``       - one row per transaction; tid is its 1-based
                           position and stays unique even when two
                           transactions are identical (rule 1: duplicate
                           transactions keep independent identity).
* ``transaction_items``  - normalized item membership, one row per
                           (tid, item); intra-transaction duplicates are
                           absent because the corpus layer removed them.
* ``jobs``               - mining jobs with their serialized resumable
                           ``MiningState`` and accounting columns.

The vertical index used by both the kernel and the independent query
validator is materialized from ``transaction_items`` with GROUP BY queries;
it is never cached inside the kernel, so validation cannot accidentally
read kernel-generated data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cfim.config import KERNEL_VERSION
from cfim.corpus import NormalizedCorpus
from cfim.errors import DomainError, ErrorCode
from cfim.kernel import (
    MiningState,
    VerticalDatabase,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS corpora (
    corpus_id                      TEXT PRIMARY KEY,
    name                           TEXT NOT NULL UNIQUE,
    transaction_count              INTEGER NOT NULL,
    item_count                     INTEGER NOT NULL,
    empty_transaction_count        INTEGER NOT NULL,
    duplicate_item_occurrences     INTEGER NOT NULL,
    kernel_version                 TEXT NOT NULL,
    created_at                     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    corpus_id   TEXT NOT NULL REFERENCES corpora(corpus_id) ON DELETE CASCADE,
    tid         INTEGER NOT NULL,
    item_count  INTEGER NOT NULL,
    PRIMARY KEY (corpus_id, tid)
);

CREATE TABLE IF NOT EXISTS transaction_items (
    corpus_id   TEXT NOT NULL REFERENCES corpora(corpus_id) ON DELETE CASCADE,
    tid         INTEGER NOT NULL,
    item        TEXT NOT NULL,
    PRIMARY KEY (corpus_id, tid, item)
);

CREATE INDEX IF NOT EXISTS idx_ti_item
    ON transaction_items (corpus_id, item);

CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    corpus_id         TEXT NOT NULL REFERENCES corpora(corpus_id) ON DELETE CASCADE,
    min_support       INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED')),
    nodes_visited     INTEGER NOT NULL,
    total_budget_used INTEGER NOT NULL,
    closed_count      INTEGER NOT NULL,
    state_json        TEXT NOT NULL,
    kernel_version    TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_corpus ON jobs (corpus_id);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _merge_intersect(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    """Local tidset intersection for the independent validation index.

    Deliberately implemented here (rather than imported from the kernel) so
    that validation cannot silently reuse any mining-kernel code path.
    """
    i = j = 0
    out: list[int] = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return tuple(out)


def _merge_covers(container: tuple[int, ...], contained: tuple[int, ...]) -> bool:
    """True iff every tid in ``contained`` occurs in ``container`` (ascending)."""
    if len(container) < len(contained):
        return False
    i = j = 0
    while i < len(contained):
        target = contained[i]
        while j < len(container) and container[j] < target:
            j += 1
        if j >= len(container) or container[j] != target:
            return False
        i += 1
    return True


@dataclass(frozen=True)
class CorpusRecord:
    corpus_id: str
    name: str
    transaction_count: int
    item_count: int
    empty_transaction_count: int
    duplicate_item_occurrences: int
    kernel_version: str
    created_at: str


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    corpus_id: str
    min_support: int
    status: str
    nodes_visited: int
    total_budget_used: int
    closed_count: int
    kernel_version: str
    created_at: str
    updated_at: str


class Store:
    """Thread-safe SQLite wrapper. One connection guarded by a write lock."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize writers; readers use autocommit snapshots."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- corpora -----------------------------------------------------------

    def create_corpus(self, corpus: NormalizedCorpus) -> CorpusRecord:
        corpus_id = uuid.uuid4().hex
        now = _utcnow()
        try:
            with self.transaction() as conn:
                conn.execute(
                    """INSERT INTO corpora(corpus_id, name, transaction_count,
                           item_count, empty_transaction_count,
                           duplicate_item_occurrences, kernel_version, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        corpus_id,
                        corpus.name,
                        len(corpus.transactions),
                        len(corpus.item_domain),
                        corpus.empty_transaction_count,
                        corpus.duplicate_item_occurrences,
                        KERNEL_VERSION,
                        now,
                    ),
                )
                tx_rows = []
                item_rows = []
                for position, items in enumerate(corpus.transactions):
                    tid = position + 1
                    tx_rows.append((corpus_id, tid, len(items)))
                    item_rows.extend((corpus_id, tid, item) for item in items)
                conn.executemany(
                    "INSERT INTO transactions(corpus_id, tid, item_count) VALUES (?, ?, ?)",
                    tx_rows,
                )
                conn.executemany(
                    "INSERT INTO transaction_items(corpus_id, tid, item) VALUES (?, ?, ?)",
                    item_rows,
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                f"corpus name {corpus.name!r} already exists",
                {"name": corpus.name},
            ) from exc
        return self.get_corpus(corpus_id)  # type: ignore[return-value]

    def get_corpus(self, corpus_id: str) -> CorpusRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)
            ).fetchone()
        if row is None:
            raise DomainError(
                ErrorCode.CORPUS_NOT_FOUND,
                f"corpus {corpus_id!r} does not exist",
                {"corpus_id": corpus_id},
            )
        return _corpus_record(row)

    def list_corpora(self) -> list[CorpusRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM corpora ORDER BY created_at, corpus_id"
            ).fetchall()
        return [_corpus_record(r) for r in rows]

    def item_domain(self, corpus_id: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT DISTINCT item FROM transaction_items
                   WHERE corpus_id = ? ORDER BY item""",
                (corpus_id,),
            ).fetchall()
        return tuple(r["item"] for r in rows)

    def load_vertical_database(self, corpus_id: str) -> VerticalDatabase:
        """Rebuild the vertical index from SQLite (used to seed a job)."""
        record = self.get_corpus(corpus_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT item, GROUP_CONCAT(tid) AS tids
                   FROM (
                       SELECT item, tid FROM transaction_items
                       WHERE corpus_id = ? ORDER BY item, tid
                   ) GROUP BY item ORDER BY item""",
                (corpus_id,),
            ).fetchall()
        items: tuple[str, ...] = tuple(r["item"] for r in rows)
        tidsets: tuple[tuple[int, ...], ...] = tuple(
            tuple(int(t) for t in r["tids"].split(",")) if r["tids"] else ()
            for r in rows
        )
        return VerticalDatabase(
            items=items,
            tidsets=tidsets,
            transaction_count=record.transaction_count,
        )

    # -- independent validation index -------------------------------------

    def tidset_for_itemset(
        self, corpus_id: str, items: tuple[str, ...]
    ) -> tuple[int, ...]:
        """Compute an itemset's tidset directly from SQL.

        Independent of the kernel: intersects per-item tid lists assembled by
        GROUP_CONCAT. Empty tuple items means "no constraint"; callers that
        mean the empty itemset should use the corpus transaction count.
        """
        if not items:
            return ()
        with self._lock:
            rows = self._conn.execute(
                """SELECT item, GROUP_CONCAT(tid) AS tids FROM (
                       SELECT item, tid FROM transaction_items
                       WHERE corpus_id = ? AND item IN (%s)
                       ORDER BY item, tid
                   ) GROUP BY item"""
                % ",".join("?" for _ in items),
                (corpus_id, *items),
            ).fetchall()
        found = {r["item"] for r in rows}
        missing = [i for i in items if i not in found]
        if missing:
            raise DomainError(
                ErrorCode.ITEM_NOT_IN_DOMAIN,
                f"items not present in corpus item domain: {missing}",
                {"unknown_items": missing},
            )
        tidsets = [
            tuple(int(t) for t in r["tids"].split(","))
            for r in sorted(rows, key=lambda r: r["item"])
        ]
        result = tidsets[0]
        for column in tidsets[1:]:
            result = _merge_intersect(result, column)
        return result

    def closure_of_itemset(
        self, corpus_id: str, items: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Closure cl(items) computed from the independent SQL index.

        ``cl(I) = { j : T(j) ⊇ T(I) }``. Used by the query endpoint to judge
        closedness without consulting any job's kernel-produced results.
        """
        db = self.load_vertical_database(corpus_id)
        domain = set(db.items)
        unknown = [item for item in items if item not in domain]
        if unknown:
            raise DomainError(
                ErrorCode.ITEM_NOT_IN_DOMAIN,
                f"items not present in corpus item domain: {unknown}",
                {"unknown_items": unknown},
            )
        target = self.tidset_for_itemset(corpus_id, items)
        return tuple(
            item
            for item, column in zip(db.items, db.tidsets)
            if _merge_covers(column, target)
        )

    # -- jobs --------------------------------------------------------------

    def create_job(self, corpus_id: str, min_support: int, state: MiningState) -> str:
        self.get_corpus(corpus_id)
        job_id = uuid.uuid4().hex
        now = _utcnow()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO jobs(job_id, corpus_id, min_support, status,
                       nodes_visited, total_budget_used, closed_count,
                       state_json, kernel_version, created_at, updated_at)
                   VALUES (?, ?, ?, 'RUNNING', 0, 0, 0, ?, ?, ?, ?)""",
                (
                    job_id,
                    corpus_id,
                    min_support,
                    json.dumps(state.to_json(), separators=(",", ":")),
                    KERNEL_VERSION,
                    now,
                    now,
                ),
            )
        return job_id

    def save_job_state(self, job_id: str, state: MiningState) -> None:
        now = _utcnow()
        status = "COMPLETED" if state.completed else "RUNNING"
        with self.transaction() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, nodes_visited = ?,
                       total_budget_used = ?, closed_count = ?,
                       state_json = ?, updated_at = ?
                   WHERE job_id = ?""",
                (
                    status,
                    state.nodes_visited,
                    state.total_budget_used,
                    len(state.results),
                    json.dumps(state.to_json(), separators=(",", ":")),
                    now,
                    job_id,
                ),
            )

    def get_job(self, job_id: str) -> JobRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise DomainError(
                ErrorCode.JOB_NOT_FOUND,
                f"job {job_id!r} does not exist",
                {"job_id": job_id},
            )
        return _job_record(row)

    def list_jobs(self, corpus_id: str | None = None) -> list[JobRecord]:
        with self._lock:
            if corpus_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at, job_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE corpus_id = ? ORDER BY created_at, job_id",
                    (corpus_id,),
                ).fetchall()
        return [_job_record(r) for r in rows]

    def load_mining_state(self, job: JobRecord) -> MiningState:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM jobs WHERE job_id = ?", (job.job_id,)
            ).fetchone()
        if job.kernel_version != KERNEL_VERSION:
            raise DomainError(
                ErrorCode.STATE_VERSION_MISMATCH,
                f"job was written by kernel {job.kernel_version!r}; running "
                f"kernel is {KERNEL_VERSION!r}",
                {"job_version": job.kernel_version, "running_version": KERNEL_VERSION},
            )
        return MiningState.from_json(json.loads(row["state_json"]))

    def state_snapshot(self, job_id: str) -> tuple[JobRecord, MiningState]:
        job = self.get_job(job_id)
        return job, self.load_mining_state(job)


def _corpus_record(row: sqlite3.Row) -> CorpusRecord:
    return CorpusRecord(
        corpus_id=row["corpus_id"],
        name=row["name"],
        transaction_count=row["transaction_count"],
        item_count=row["item_count"],
        empty_transaction_count=row["empty_transaction_count"],
        duplicate_item_occurrences=row["duplicate_item_occurrences"],
        kernel_version=row["kernel_version"],
        created_at=row["created_at"],
    )


def _job_record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        corpus_id=row["corpus_id"],
        min_support=row["min_support"],
        status=row["status"],
        nodes_visited=row["nodes_visited"],
        total_budget_used=row["total_budget_used"],
        closed_count=row["closed_count"],
        kernel_version=row["kernel_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
