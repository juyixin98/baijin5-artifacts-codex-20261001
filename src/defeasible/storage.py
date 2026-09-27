"""Persistent evidence store backed by SQLite.

The store owns *ground evidence literals* only.  Rules and priorities live
in the rule layer; keeping evidence separate from the theory is what makes
"missing evidence is not the opposite fact" enforceable -- the store can
answer "what is known?" but never invents ``-P`` because ``P`` is absent.

Supports multiple named cases (``case_id``), so test fixtures and sample
data coexist without interference.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .errors import InvalidInputError
from .language import Term

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id     TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence (
    case_id   TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    literal   TEXT NOT NULL,
    predicate TEXT NOT NULL,
    negated   INTEGER NOT NULL,
    args_json TEXT NOT NULL,
    PRIMARY KEY (case_id, literal)
);

CREATE TABLE IF NOT EXISTS theories (
    case_id     TEXT PRIMARY KEY REFERENCES cases(case_id) ON DELETE CASCADE,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class EvidenceStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI may handle requests on a pool;
        # SQLite connections here are short-lived per operation.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ----- lifecycle -----

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- cases -----

    def create_case(self, case_id: str, description: str = "") -> None:
        if not case_id:
            raise InvalidInputError("case_id must be non-empty")
        try:
            self._conn.execute(
                "INSERT INTO cases(case_id, description) VALUES (?, ?)",
                (case_id, description),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise InvalidInputError(f"case {case_id!r} already exists") from e

    def ensure_case(self, case_id: str, description: str = "") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO cases(case_id, description) VALUES (?, ?)",
            (case_id, description),
        )
        self._conn.commit()

    def list_cases(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT case_id, description, created_at FROM cases ORDER BY case_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_case(self, case_id: str) -> None:
        self._conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self._conn.commit()

    # ----- evidence -----

    def add_evidence(self, case_id: str, literals: Iterable[str | Term]) -> int:
        self._require_case(case_id)
        rows = []
        for lit in literals:
            term = lit if isinstance(lit, Term) else Term.parse(str(lit))
            if not term.is_ground:
                raise InvalidInputError(
                    f"evidence {term.literal!r} must be ground (no variables)"
                )
            rows.append(
                (
                    case_id,
                    term.literal,
                    term.predicate,
                    int(term.negated),
                    json.dumps(list(term.args)),
                )
            )
        if not rows:
            return 0
        try:
            self._conn.executemany(
                "INSERT OR IGNORE INTO evidence"
                "(case_id, literal, predicate, negated, args_json) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise InvalidInputError(f"unknown case {case_id!r}") from e
        return len(rows)

    def remove_evidence(self, case_id: str, literal: str) -> None:
        term = Term.parse(literal)
        self._conn.execute(
            "DELETE FROM evidence WHERE case_id = ? AND literal = ?",
            (case_id, term.literal),
        )
        self._conn.commit()

    def load_evidence(self, case_id: str) -> list[Term]:
        self._require_case(case_id)
        rows = self._conn.execute(
            "SELECT literal FROM evidence WHERE case_id = ? ORDER BY literal",
            (case_id,),
        ).fetchall()
        return [Term.parse(r["literal"]) for r in rows]

    # ----- theories -----

    def save_theory(self, case_id: str, theory_dict: dict) -> None:
        self._require_case(case_id)
        self._conn.execute(
            "INSERT INTO theories(case_id, payload) VALUES (?, ?) "
            "ON CONFLICT(case_id) DO UPDATE SET payload = excluded.payload, "
            "updated_at = datetime('now')",
            (case_id, json.dumps(theory_dict, ensure_ascii=False, sort_keys=True)),
        )
        self._conn.commit()

    def load_theory(self, case_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT payload FROM theories WHERE case_id = ?", (case_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    # ----- helpers -----

    def _require_case(self, case_id: str) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise InvalidInputError(f"unknown case {case_id!r}")
