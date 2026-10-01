"""Append-only SQLite evidence store.

Guarantees
----------
* Decisions are immutable rows.  There is deliberately no UPDATE/DELETE path
  in this module; a p-value submitted later cannot rewrite an earlier row or
  the budget that was spent on it.
* Each row carries a SHA-256 hash over its decision fields, the frozen
  contract fingerprint and the previous row's hash (a per-run hash chain).
* Hot-path state (tau, W(tau), wealth, index) is kept on the run row for
  O(1) decisions.  :mod:`app.diagnostics` independently replays every stored
  p-value to prove those counters and every threshold are consistent.
* A new store instance on the same file is a restart: all state comes back
  from SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

from .contracts import (
    CONTRACT_FINGERPRINT,
    DEFAULT_MAX_DECISIONS,
    HARD_MAX_DECISIONS,
    LORD3State,
    RULE_VERSION,
    Decision,
    step,
)
from .errors import (
    DuplicateHypothesisError,
    EvidenceTamperedError,
    RunLimitReachedError,
    RunNotFoundError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    rule_version         TEXT NOT NULL,
    contract_fingerprint TEXT NOT NULL,
    max_decisions        INTEGER NOT NULL,
    tau                  INTEGER NOT NULL,
    w_tau                REAL NOT NULL,
    wealth               REAL NOT NULL,
    last_index           INTEGER NOT NULL,
    status               TEXT NOT NULL,
    created_at           REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    idx           INTEGER NOT NULL,
    hypothesis_id TEXT NOT NULL,
    p_value       REAL NOT NULL,
    threshold     REAL NOT NULL,
    gamma_value   REAL NOT NULL,
    rejected      INTEGER NOT NULL,
    wealth_before REAL NOT NULL,
    wealth_after  REAL NOT NULL,
    tau           INTEGER NOT NULL,
    w_tau_used    REAL NOT NULL,
    reason        TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    row_hash      TEXT NOT NULL,
    decided_at    REAL NOT NULL,
    PRIMARY KEY (run_id, idx),
    UNIQUE (run_id, hypothesis_id)
);
CREATE INDEX IF NOT EXISTS decisions_run_idx ON decisions(run_id, idx);
"""

GENESIS_HASH = "0" * 64


def row_hash(
    run_id: str,
    decision: Decision,
    prev_hash: str,
    contract_fingerprint: str = CONTRACT_FINGERPRINT,
) -> str:
    """SHA-256 over the canonical JSON of a decision row.

    Floats are serialized with Python's round-trip repr so the hash is stable
    across processes.
    """
    payload = {
        "run_id": run_id,
        "idx": decision.index,
        "hypothesis_id": decision.hypothesis_id,
        "p_value": decision.p_value,
        "threshold": decision.threshold,
        "gamma_value": decision.gamma_value,
        "rejected": int(decision.rejected),
        "wealth_before": decision.wealth_before,
        "wealth_after": decision.wealth_after,
        "tau": decision.tau,
        "w_tau_used": decision.w_tau_used,
        "prev_hash": prev_hash,
        "contract_fingerprint": contract_fingerprint,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True, slots=True)
class RunMeta:
    run_id: str
    rule_version: str
    contract_fingerprint: str
    max_decisions: int
    tau: int
    w_tau: float
    wealth: float
    last_index: int
    status: str
    created_at: float


class SQLiteStore:
    """Thread-safe, file-backed evidence store.  Use ':memory:' for tests."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._seen_cache: dict[str, set[str]] = {}
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ runs

    def create_run(
        self, max_decisions: int = DEFAULT_MAX_DECISIONS
    ) -> RunMeta:
        if (
            not isinstance(max_decisions, int)
            or isinstance(max_decisions, bool)
            or not (1 <= max_decisions <= HARD_MAX_DECISIONS)
        ):
            raise ValueError(
                f"max_decisions must be an int in [1, {HARD_MAX_DECISIONS}]"
            )
        run_id = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, RULE_VERSION, CONTRACT_FINGERPRINT, max_decisions,
                    0, 0.005, 0.005, 0, "open", now,
                ),
            )
            self._seen_cache[run_id] = set()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunMeta:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError({"run_id": run_id})
        return _meta_from_row(row)

    def list_run_ids(self) -> list[str]:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT run_id FROM runs ORDER BY created_at"
            ).fetchall()
        ]

    # --------------------------------------------------------------- decision

    def append_decision(
        self, run_id: str, hypothesis_id: str, p_value: float
    ) -> dict:
        """Apply one frozen LORD 3 step and append it as irrevocable evidence."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RunNotFoundError({"run_id": run_id})
                if row["last_index"] >= row["max_decisions"]:
                    raise RunLimitReachedError(
                        {
                            "run_id": run_id,
                            "max_decisions": row["max_decisions"],
                        }
                    )
                seen = self._load_seen(run_id)
                if hypothesis_id in seen:
                    raise DuplicateHypothesisError(
                        {"run_id": run_id, "hypothesis_id": hypothesis_id,
                         "index": row["last_index"] + 1}
                    )

                state = LORD3State(
                    tau=row["tau"],
                    w_tau=row["w_tau"],
                    wealth=row["wealth"],
                    last_index=row["last_index"],
                    seen_ids=frozenset(seen),
                    max_decisions=row["max_decisions"],
                )
                new_state, decision = step(state, hypothesis_id, p_value)
                prev = self._prev_hash(run_id)
                rhash = row_hash(run_id, decision, prev)

                self._conn.execute(
                    "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, decision.index, decision.hypothesis_id,
                        decision.p_value, decision.threshold,
                        decision.gamma_value, int(decision.rejected),
                        decision.wealth_before, decision.wealth_after,
                        decision.tau, decision.w_tau_used, decision.reason,
                        prev, rhash, time.time(),
                    ),
                )
                self._conn.execute(
                    "UPDATE runs SET tau=?, w_tau=?, wealth=?, last_index=? "
                    "WHERE run_id=?",
                    (
                        new_state.tau, new_state.w_tau, new_state.wealth,
                        new_state.last_index, run_id,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            seen.add(hypothesis_id)
            return self.decision_dict(run_id, decision.index, rhash)

    def list_decisions(
        self, run_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        self.get_run(run_id)
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY idx LIMIT ? OFFSET ?",
            (run_id, limit, offset),
        ).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["rejected"] = bool(r["rejected"])
        return out

    def decision_dict(self, run_id: str, idx: int, row_hash_value: str) -> dict:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE run_id=? AND idx=?", (run_id, idx)
        ).fetchone()
        if row is None:  # pragma: no cover - defensive, row just inserted
            raise EvidenceTamperedError({"run_id": run_id, "idx": idx})
        out = dict(row)
        out["rejected"] = bool(out["rejected"])
        out["row_hash"] = row_hash_value
        return out

    def all_decision_rows(self, run_id: str) -> list[sqlite3.Row]:
        self.get_run(run_id)
        return self._conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY idx", (run_id,)
        ).fetchall()

    # -------------------------------------------------------------- internals

    def _load_seen(self, run_id: str) -> set[str]:
        cached = self._seen_cache.get(run_id)
        if cached is not None:
            return cached
        ids = {
            r[0]
            for r in self._conn.execute(
                "SELECT hypothesis_id FROM decisions WHERE run_id=?",
                (run_id,),
            ).fetchall()
        }
        self._seen_cache[run_id] = ids
        return ids

    def _prev_hash(self, run_id: str) -> str:
        row = self._conn.execute(
            "SELECT row_hash FROM decisions WHERE run_id=? ORDER BY idx DESC "
            "LIMIT 1",
            (run_id,),
        ).fetchone()
        return GENESIS_HASH if row is None else row["row_hash"]


def _meta_from_row(row: sqlite3.Row) -> RunMeta:
    return RunMeta(
        run_id=row["run_id"],
        rule_version=row["rule_version"],
        contract_fingerprint=row["contract_fingerprint"],
        max_decisions=row["max_decisions"],
        tau=row["tau"],
        w_tau=row["w_tau"],
        wealth=row["wealth"],
        last_index=row["last_index"],
        status=row["status"],
        created_at=row["created_at"],
    )
