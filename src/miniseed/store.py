"""SQLite-backed seed index (repository layer).

Schema is parameter-scoped per run: a run pins ``(k, w, hash_version)`` and
queries against that run must use identical parameters, otherwise they are
rejected with ``PARAMETER_CONFLICT`` rather than silently returning garbage.

Tables
------
``runs``     one row per indexed dataset (run identity + parameter provenance)
``seeds``    one row per deduplicated minimizer occurrence
                 (run_id, hash, ref_offset, window_index, canonical, orient)
``audit``    append-only event log tying every mutation/query to its run/input

Repetitive-minimizer guard: a ``(run_id, hash)`` bucket may hold at most
``max_bucket_size`` offsets; an insert that would exceed it raises
``BUCKET_OVERFLOW`` so a low-complexity reference fails loudly instead of
exploding candidate counts.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import ErrorCode, MiniseedError
from .hashing import HASH_VERSION
from .minimizer import Minimizer


@dataclass(frozen=True)
class SeedRow:
    run_id: str
    hash: int
    ref_offset: int
    window_index: int
    canonical: str
    orientation: str


@dataclass(frozen=True)
class CandidateHit:
    """One seed overlap between a query read and the reference.

    This is a *candidate* -- a shared minimizer occurrence -- not a full
    alignment. Downstream callers must verify with alignment/extension.
    """

    ref_offset: int
    query_offset: int
    window_index: int
    hash: int
    canonical: str
    ref_orientation: str
    query_orientation: str

    @property
    def strand_consistent(self) -> bool:
        """True iff both strands report the same canonical orientation."""
        return self.ref_orientation == self.query_orientation


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    k             INTEGER NOT NULL,
    w             INTEGER NOT NULL,
    hash_version  TEXT NOT NULL,
    ref_name      TEXT NOT NULL,
    ref_length    INTEGER NOT NULL,
    seed_count    INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS seeds (
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    hash         INTEGER NOT NULL,
    ref_offset   INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    canonical    TEXT NOT NULL,
    orientation  TEXT NOT NULL,
    PRIMARY KEY (run_id, hash, ref_offset)
);
CREATE INDEX IF NOT EXISTS idx_seeds_lookup
    ON seeds (run_id, hash);
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT,
    event     TEXT NOT NULL,
    identity  TEXT NOT NULL,
    detail    TEXT NOT NULL,
    at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit (run_id, id);
"""


class SeedStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False is safe: FastAPI handlers are serialized per
        # request and every write runs inside an immediate transaction.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SeedStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------------------------------------------------------------- runs
    def get_run(self, run_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise MiniseedError(
                ErrorCode.RUN_NOT_FOUND,
                f"run {run_id!r} does not exist",
                context={"run_id": run_id},
            )
        return row

    def create_run(
        self,
        run_id: str,
        *,
        k: int,
        w: int,
        ref_name: str,
        ref_length: int,
        overwrite: bool = False,
    ) -> None:
        existing = self._conn.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing and not overwrite:
            raise MiniseedError(
                ErrorCode.RUN_ALREADY_EXISTS,
                f"run {run_id!r} already exists",
                context={"run_id": run_id},
            )
        with self.transaction() as conn:
            conn.execute("DELETE FROM seeds WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.execute(
                "INSERT INTO runs (run_id, k, w, hash_version, ref_name,"
                " ref_length, seed_count) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (run_id, k, w, HASH_VERSION, ref_name, ref_length),
            )

    def delete_run(self, run_id: str) -> None:
        """Remove run metadata and its seeds (audit history is retained)."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM seeds WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    # --------------------------------------------------------------- seeds
    def bulk_insert_seeds(
        self, run_id: str, seeds: list[Minimizer], max_bucket_size: int
    ) -> int:
        """Insert deduped minimizers, enforcing the repetitive-bucket cap."""
        if not seeds:
            raise MiniseedError(
                ErrorCode.EMPTY_INDEX,
                f"run {run_id!r} produced no seeds "
                "(reference too short or entirely N)",
                context={"run_id": run_id},
            )
        bucket_counts: dict[int, int] = {}
        rows: list[SeedRow] = []
        for m in seeds:
            n = bucket_counts.get(m.hash, 0) + 1
            if n > max_bucket_size:
                raise MiniseedError(
                    ErrorCode.BUCKET_OVERFLOW,
                    f"minimizer bucket {m.hash} exceeds the "
                    f"{max_bucket_size}-offset low-complexity cap",
                    context={
                        "run_id": run_id,
                        "hash": m.hash,
                        "canonical": m.canonical,
                        "bucket_size": n,
                        "max_bucket_size": max_bucket_size,
                    },
                )
            bucket_counts[m.hash] = n
            rows.append(
                SeedRow(
                    run_id=run_id,
                    hash=m.hash,
                    ref_offset=m.offset,
                    window_index=m.window_index,
                    canonical=m.canonical,
                    orientation=m.orientation,
                )
            )

        with self.transaction() as conn:
            conn.executemany(
                "INSERT INTO seeds (run_id, hash, ref_offset, window_index,"
                " canonical, orientation) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.run_id,
                        r.hash,
                        r.ref_offset,
                        r.window_index,
                        r.canonical,
                        r.orientation,
                    )
                    for r in rows
                ],
            )
            conn.execute(
                "UPDATE runs SET seed_count = ? WHERE run_id = ?",
                (len(rows), run_id),
            )
        return len(rows)

    # ------------------------------------------------------------ querying
    def lookup_candidates(
        self,
        run_id: str,
        query_minimizers: list[Minimizer],
        *,
        max_candidates: int,
    ) -> list[CandidateHit]:
        hits: list[CandidateHit] = []
        for q in query_minimizers:
            ref_rows = self._conn.execute(
                "SELECT ref_offset, window_index, canonical, orientation"
                " FROM seeds WHERE run_id = ? AND hash = ?"
                " ORDER BY ref_offset",
                (run_id, q.hash),
            ).fetchall()
            for r in ref_rows:
                hits.append(
                    CandidateHit(
                        ref_offset=r["ref_offset"],
                        query_offset=q.offset,
                        window_index=r["window_index"],
                        hash=q.hash,
                        canonical=r["canonical"],
                        ref_orientation=r["orientation"],
                        query_orientation=q.orientation,
                    )
                )
                if len(hits) > max_candidates:
                    raise MiniseedError(
                        ErrorCode.TOO_MANY_CANDIDATES,
                        f"candidate count exceeds cap {max_candidates}; "
                        "narrow the query or raise the configured limit",
                        context={
                            "run_id": run_id,
                            "max_candidates": max_candidates,
                        },
                    )
        return hits

    def bucket_sizes(self, run_id: str) -> dict[int, int]:
        rows = self._conn.execute(
            "SELECT hash, COUNT(*) AS c FROM seeds WHERE run_id = ?"
            " GROUP BY hash",
            (run_id,),
        ).fetchall()
        return {r["hash"]: r["c"] for r in rows}

    # --------------------------------------------------------------- audit
    def audit(
        self,
        event: str,
        identity: str,
        detail: dict,
        run_id: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit (run_id, event, identity, detail)"
                " VALUES (?, ?, ?, ?)",
                (run_id, event, identity, json.dumps(detail, sort_keys=True)),
            )

    def list_audit(self, run_id: str | None = None, limit: int = 100) -> list:
        if run_id is None:
            return self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM audit WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()

    def list_runs(self) -> list:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY run_id"
        ).fetchall()
