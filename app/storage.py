"""Append-only SQLite persistence for online-FDR runs.

Design contract enforced here:

1. **Two-phase causal commit.** A hypothesis first *reserves* a slot: its
   threshold alpha_t is computed from PAST DECIDED REJECTIONS ONLY and stored.
   The p-value arrives in a separate call and can never alter the stored
   threshold. Retrospective edits to a p-value are therefore impossible without
   tripping the integrity check.

2. **Strict ordering.** Slots are 1..H in arrival order; you cannot reserve
   t+1 while slot t is still pending, and a hypothesis id is unique within a
   run (duplicate identities are a STATE_CONFLICT, not silently re-decided).

3. **Hash chain.** Every decided row hashes the previous head, so deleting or
   altering history is detected by :meth:`RunStore.replay`.

4. **Event log.** Every reservation, decision, rejection and classified error
   is appended to an event table with run id, sequence number and key state,
   sufficient to reconstruct/replay an incident.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from .errors import (
    ComputationError,
    DomainError,
    IntegrityError,
    NotFoundError,
    ResourceExhaustedError,
    StateConflictError,
)
from .statistics import LordConfig, LordState, LordStep, validate_pvalue

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    alpha            REAL NOT NULL,
    w0               REAL NOT NULL,
    horizon          INTEGER NOT NULL,
    status           TEXT NOT NULL,           -- open | completed
    head_hash        TEXT NOT NULL,           -- hash chain head
    note             TEXT NOT NULL,
    created_at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id        TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    hypothesis_id TEXT NOT NULL,
    -- reservation phase:
    threshold     REAL NOT NULL,
    wealth_before REAL NOT NULL,
    gamma_t       REAL NOT NULL,
    reserved_at   REAL NOT NULL,
    status        TEXT NOT NULL,              -- pending | decided
    -- decision phase:
    p_value       REAL,
    rejected      INTEGER,
    wealth_after  REAL,
    decided_at    REAL,
    -- integrity:
    prev_hash     TEXT,
    row_hash      TEXT,
    PRIMARY KEY (run_id, idx),
    UNIQUE (run_id, hypothesis_id)
);
CREATE TABLE IF NOT EXISTS events (
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,                 -- info | reserve | decide | reject | error
    code       TEXT,
    message    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

GENESIS = "0" * 64
_HASH_TOL = 1e-12


def _row_hash(prev: str, fields: dict[str, Any]) -> str:
    blob = prev + json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RunStore:
    """Thread-safe SQLite-backed run store. All writes are serialized."""

    def __init__(self, path: str = ":memory:", busy_timeout_ms: int = 5000) -> None:
        self._lock = threading.RLock()
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=busy_timeout_ms / 1000.0)
        self._conn.row_factory = sqlite3.Row
        # WAL only applies to file databases; busy_timeout makes concurrent
        # multi-process writers wait instead of raising "database is locked".
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(SCHEMA)

    def _begin_write(self) -> None:
        """Acquire the write lock immediately.

        ``BEGIN IMMEDIATE`` serializes writers across connections/processes:
        once held, all reads see a stable snapshot and the optimistic UPDATE
        guard cannot be beaten by a concurrent transaction. A timeout under
        contention is reported as STATE_CONFLICT (retryable), not a raw 500.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise StateConflictError(
                "database busy under concurrent access; retry the request",
                details={"reason": str(exc)},
            ) from exc

    @contextmanager
    def _write_tx(self):
        """Atomic write transaction that takes the write lock up front."""
        self._begin_write()
        try:
            yield
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- runs --

    def create_run(
        self,
        config: LordConfig,
        note: str = "",
        run_id: str | None = None,
    ) -> str:
        rid = run_id or f"run-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO runs(run_id, contract_version, alpha, w0, horizon,"
                    " status, head_hash, note, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        rid,
                        config.contract_version,
                        config.alpha,
                        config.w0,
                        config.horizon,
                        "open",
                        GENESIS,
                        note,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StateConflictError(
                    "run id already exists", details={"run_id": rid}
                ) from exc
        self._event(rid, "info", None, "run created", {"config": asdict(config)})
        return rid

    def _get_run_row(self, run_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("unknown run", details={"run_id": run_id})
        return row

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._get_run_row(run_id))

    def config_of(self, run_id: str) -> LordConfig:
        row = self._get_run_row(run_id)
        return LordConfig(
            alpha=row["alpha"],
            w0=row["w0"],
            horizon=row["horizon"],
            contract_version=row["contract_version"],
        )

    # ------------------------------------------------------------ reserve --

    def reserve(self, run_id: str, hypothesis_id: str) -> dict[str, Any]:
        """Phase 1: issue and commit alpha_t for the next sequential slot."""
        with self._lock:
            with self._write_tx():
                row = self._get_run_row(run_id)
                self._require_unknown_identity(run_id, hypothesis_id)
                decided, pending = self._counts(run_id)
                idx = decided + 1
                # Horizon exhaustion takes precedence over run status: a run
                # that consumed its frozen budget reports RESOURCE_EXHAUSTED.
                if idx > row["horizon"]:
                    raise ResourceExhaustedError(
                        "frozen horizon reached",
                        details={"horizon": row["horizon"], "requested_index": idx},
                    )
                if row["status"] != "open":
                    raise StateConflictError(
                        "run is not open", details={"run_id": run_id, "status": row["status"]}
                    )
                if pending:
                    raise StateConflictError(
                        "previous hypothesis is still pending a p-value",
                        details={"pending_slots": pending},
                    )
                state = self._reconstruct_state(run_id)
                # Threshold depends on past committed decisions only.
                threshold = state.preview_threshold()
                wealth_before = state.wealth
                gamma_t = float(state.schedule[idx - 1])
                try:
                    self._conn.execute(
                        "INSERT INTO steps(run_id, idx, hypothesis_id, threshold,"
                        " wealth_before, gamma_t, reserved_at, status)"
                        " VALUES (?,?,?,?,?,?,?, 'pending')",
                        (
                            run_id,
                            idx,
                            hypothesis_id,
                            threshold,
                            wealth_before,
                            gamma_t,
                            time.time(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    # With BEGIN IMMEDIATE the explicit checks above normally
                    # catch this; the constraint is the last line of defense.
                    raise StateConflictError(
                        "reservation conflicts with existing state "
                        "(duplicate hypothesis id or slot index)",
                        details={"hypothesis_id": hypothesis_id, "idx": idx},
                    ) from exc
        self._event(
            run_id,
            "reserve",
            None,
            f"threshold committed for slot {idx}",
            {
                "idx": idx,
                "hypothesis_id": hypothesis_id,
                "threshold": threshold,
                "wealth_before": state.wealth,
            },
        )
        return self.get_step(run_id, idx)

    # ------------------------------------------------------------- decide --

    def decide(self, run_id: str, hypothesis_id: str, p_value: Any) -> dict[str, Any]:
        """Phase 2: submit the p-value for an already-reserved hypothesis."""
        with self._lock:
            with self._write_tx():
                self._get_run_row(run_id)
                row = self._conn.execute(
                    "SELECT * FROM steps WHERE run_id = ? AND hypothesis_id = ?",
                    (run_id, hypothesis_id),
                ).fetchone()
                if row is None:
                    raise StateConflictError(
                        "no reservation for this hypothesis id; reserve a threshold"
                        " before submitting a p-value",
                        details={"hypothesis_id": hypothesis_id},
                    )
                if row["status"] == "decided":
                    # Takes precedence over p-value validation: a post-hoc
                    # rewrite attempt is a state conflict for ANY payload.
                    raise StateConflictError(
                        "hypothesis already decided; past budgets cannot be rewritten",
                        details={"hypothesis_id": hypothesis_id, "idx": row["idx"]},
                    )
                idx = row["idx"]
                decided, _ = self._counts(run_id)
                if idx != decided + 1:
                    raise StateConflictError(
                        "out-of-order decision",
                        details={"idx": idx, "next_expected": decided + 1},
                    )
                p = validate_pvalue(p_value)  # INPUT_ERROR only for live slots
                # Reconstruct the kernel from committed history and run the
                # decision through it, then cross-check against the stored,
                # pre-committed threshold.
                state = self._reconstruct_state(run_id)
                step = state.step(p)
                if step.index != idx:
                    raise ComputationError(
                        "kernel index mismatch",
                        details={"kernel_index": step.index, "slot_index": idx},
                    )
                if not math.isclose(
                    step.threshold, row["threshold"], rel_tol=_HASH_TOL, abs_tol=_HASH_TOL
                ):
                    raise ComputationError(
                        "stored threshold disagrees with recomputation",
                        details={
                            "stored": row["threshold"],
                            "recomputed": step.threshold,
                        },
                    )
                run_row = self._get_run_row(run_id)
                prev = run_row["head_hash"]
                decided_at = time.time()
                # The hash binds EVERY persisted decision field, including the
                # post-decision wealth and timestamps, so no column can be
                # altered without breaking the chain.
                h = _row_hash(
                    prev,
                    {
                        "idx": idx,
                        "hypothesis_id": hypothesis_id,
                        "threshold": step.threshold,
                        "wealth_before": step.wealth_before,
                        "gamma_t": step.gamma_t,
                        "p_value": p,
                        "rejected": step.rejected,
                        "wealth_after": step.wealth_after,
                        "decided_at": decided_at,
                    },
                )
                # Optimistic guard: exactly one decide can flip pending->decided.
                cur = self._conn.execute(
                    "UPDATE steps SET status='decided', p_value=?, rejected=?,"
                    " wealth_after=?, decided_at=?, prev_hash=?, row_hash=?"
                    " WHERE run_id=? AND idx=? AND status='pending'",
                    (
                        p,
                        1 if step.rejected else 0,
                        step.wealth_after,
                        decided_at,
                        prev,
                        h,
                        run_id,
                        idx,
                    ),
                )
                if cur.rowcount != 1:
                    # Another connection/process decided this slot first.
                    raise StateConflictError(
                        "slot was decided concurrently; budget already spent",
                        details={"idx": idx, "hypothesis_id": hypothesis_id},
                    )
                self._conn.execute(
                    "UPDATE runs SET head_hash=? WHERE run_id=?", (h, run_id)
                )
                if idx >= run_row["horizon"]:
                    self._conn.execute(
                        "UPDATE runs SET status='completed' WHERE run_id=?", (run_id,)
                    )
        self._event(
            run_id,
            "decide",
            None,
            f"slot {idx} decided; rejected={step.rejected}",
            {
                "idx": idx,
                "hypothesis_id": hypothesis_id,
                "p_value": p,
                "threshold": step.threshold,
                "wealth_before": step.wealth_before,
                "wealth_after": step.wealth_after,
                "rejected": step.rejected,
            },
        )
        if step.rejected:
            self._event(
                run_id,
                "reject",
                None,
                f"rejection at slot {idx}",
                {"idx": idx, "p_value": p, "threshold": step.threshold},
            )
        return self.get_step(run_id, idx)

    # ------------------------------------------------------------- reads --

    def get_step(self, run_id: str, idx: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM steps WHERE run_id=? AND idx=?", (run_id, idx)
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    "unknown step", details={"run_id": run_id, "idx": idx}
                )
            return dict(row)

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._get_run_row(run_id)
            rows = self._conn.execute(
                "SELECT * FROM steps WHERE run_id=? ORDER BY idx", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ replay --

    def replay(self, run_id: str) -> dict[str, Any]:
        """Independently recompute the whole history and audit the chain.

        Returns a diagnostic report. Raises INTEGRITY_ERROR if stored
        thresholds, decisions or hashes disagree with recomputation.
        """
        with self._lock:
            run = self._get_run_row(run_id)
            steps = self.list_steps(run_id)
            config = self.config_of(run_id)
            state = LordState.initialize(config)
            prev = GENESIS
            checked = []
            for s in steps:
                if s["status"] != "decided":
                    continue
                idx = s["idx"]
                if idx != state.steps_taken + 1:
                    raise IntegrityError(
                        "gap in decided history", details={"first_bad_index": idx}
                    )

                def _as_integrity(fn):
                    """Run a kernel call during an audit; any domain fault caused
                    by corrupted stored data is an integrity failure, never a
                    fresh-input INPUT/RESOURCE class."""
                    try:
                        return fn()
                    except IntegrityError:
                        raise
                    except DomainError as exc:
                        raise IntegrityError(
                            "stored row cannot be replayed through the frozen kernel",
                            details={"idx": idx, "underlying_code": exc.code},
                        ) from exc

                threshold = _as_integrity(state.preview_threshold)
                wealth_before = _as_integrity(lambda: state.wealth)
                gamma_t = float(state.schedule[idx - 1])
                if not math.isclose(
                    threshold, s["threshold"], rel_tol=1e-12, abs_tol=1e-12
                ):
                    raise IntegrityError(
                        "stored threshold not derivable from past results",
                        details={"idx": idx, "stored": s["threshold"], "recomputed": threshold},
                    )
                if not math.isclose(
                    wealth_before, s["wealth_before"], rel_tol=1e-12, abs_tol=1e-12
                ):
                    raise IntegrityError(
                        "stored pre-decision wealth not derivable from past results",
                        details={
                            "idx": idx,
                            "stored": s["wealth_before"],
                            "recomputed": wealth_before,
                        },
                    )
                if not math.isclose(gamma_t, s["gamma_t"], rel_tol=1e-12, abs_tol=1e-15):
                    raise IntegrityError(
                        "stored gamma disagrees with frozen schedule",
                        details={"idx": idx, "stored": s["gamma_t"], "recomputed": gamma_t},
                    )
                step: LordStep = _as_integrity(lambda: state.step(s["p_value"]))
                if bool(step.rejected) != bool(s["rejected"]):
                    raise IntegrityError(
                        "stored rejection disagrees with threshold comparison",
                        details={"idx": idx, "stored": bool(s["rejected"]), "recomputed": step.rejected},
                    )
                if not math.isclose(
                    step.wealth_after, s["wealth_after"], rel_tol=1e-12, abs_tol=1e-12
                ):
                    raise IntegrityError(
                        "stored post-decision wealth disagrees with recomputation",
                        details={
                            "idx": idx,
                            "stored": s["wealth_after"],
                            "recomputed": step.wealth_after,
                        },
                    )
                # Rebuild the hash over the FULL persisted record. The stored
                # timestamp is part of the record, so editing it breaks the link.
                h = _row_hash(
                    prev,
                    {
                        "idx": idx,
                        "hypothesis_id": s["hypothesis_id"],
                        "threshold": s["threshold"],
                        "wealth_before": s["wealth_before"],
                        "gamma_t": s["gamma_t"],
                        "p_value": s["p_value"],
                        "rejected": bool(s["rejected"]),
                        "wealth_after": s["wealth_after"],
                        "decided_at": s["decided_at"],
                    },
                )
                if s["prev_hash"] != prev or s["row_hash"] != h:
                    raise IntegrityError(
                        "hash chain broken", details={"idx": idx}
                    )
                prev = h
                checked.append(
                    {
                        "idx": idx,
                        "hypothesis_id": s["hypothesis_id"],
                        "threshold": s["threshold"],
                        "p_value": s["p_value"],
                        "rejected": bool(s["rejected"]),
                        "wealth_before": s["wealth_before"],
                    }
                )
            if prev != run["head_hash"]:
                raise IntegrityError(
                    "run head hash disagrees with replay", details={}
                )
            # Run status must agree with how far the frozen horizon was consumed.
            expect_completed = len(checked) >= run["horizon"]
            if (run["status"] == "completed") != expect_completed:
                raise IntegrityError(
                    "run completion status disagrees with decided count",
                    details={
                        "decided": len(checked),
                        "horizon": run["horizon"],
                        "stored_status": run["status"],
                    },
                )
            n_rej = sum(1 for c in checked if c["rejected"])
            return {
                "run_id": run_id,
                "contract_version": run["contract_version"],
                "decisions_checked": len(checked),
                "rejections": n_rej,
                "head_hash": prev,
                "steps": checked,
            }

    # ------------------------------------------------------------ events --

    def _event(
        self,
        run_id: str,
        kind: str,
        code: str | None,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock, self._conn:
            seq = (
                self._conn.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            self._conn.execute(
                "INSERT INTO events(run_id, seq, kind, code, message, payload, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    seq,
                    kind,
                    code,
                    message,
                    json.dumps(payload, sort_keys=True, default=str),
                    time.time(),
                ),
            )

    def log_error(self, run_id: str | None, code: str, message: str, payload: dict[str, Any]) -> None:
        if run_id is None:
            return  # run-scoped logging only; unknown runs have nowhere to append
        self._event(run_id, "error", code, message, payload)

    def list_events(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            self._get_run_row(run_id)
            rows = self._conn.execute(
                "SELECT seq, kind, code, message, payload, created_at FROM events"
                " WHERE run_id=? ORDER BY seq DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----------------------------------------------------------- helpers --

    def _counts(self, run_id: str) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT SUM(status='decided') AS d, SUM(status='pending') AS p"
            " FROM steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["d"] or 0), int(row["p"] or 0)

    def _require_unknown_identity(self, run_id: str, hypothesis_id: str) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM steps WHERE run_id=? AND hypothesis_id=?",
            (run_id, hypothesis_id),
        ).fetchone()
        if row is not None:
            raise StateConflictError(
                "hypothesis id already used in this run",
                details={"hypothesis_id": hypothesis_id},
            )

    def _reconstruct_state(self, run_id: str) -> LordState:
        config = self.config_of(run_id)
        state = LordState.initialize(config)
        rows = self._conn.execute(
            "SELECT idx, p_value FROM steps WHERE run_id=? AND status='decided'"
            " ORDER BY idx",
            (run_id,),
        ).fetchall()
        for r in rows:
            step = state.step(r["p_value"])
            if step.index != r["idx"]:
                raise ComputationError(
                    "reconstruction index mismatch",
                    details={"stored_idx": r["idx"], "kernel_idx": step.index},
                )
        return state
