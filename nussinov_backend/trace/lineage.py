"""Request-scoped lineage recording and retrieval.

Each request gets a :class:`LineageRecorder` that collects ordered
processing steps (name, outcome, detail), then persists the request, its
steps, structures and warnings in a single SQLite transaction. Every row
carries the request id, algorithm version and processing location, so an
API response can always be traced back to stored evidence.
"""
from __future__ import annotations

import datetime as _dt
import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import ALGORITHM_NAME, ALGORITHM_VERSION, MODEL_SCOPE
from ..domain.rules import ALLOWED_PAIR_TUPLES, MIN_LOOP_LENGTH, PSEUDOKNOTS_SUPPORTED
from ..domain.service import FoldResult
from ..errors import ResultNotFoundError
from .db import Database


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def new_request_id() -> str:
    return f"req_{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Step:
    order: int
    name: str
    outcome: str  # "ok" | "failed" | "skipped" | "warning"
    detail: str
    created_at: str


@dataclass
class LineageRecorder:
    db: Database
    request_id: str
    source: str
    started_at: float = field(default_factory=time.perf_counter)
    started_at_iso: str = field(default_factory=utc_now)
    steps: list[Step] = field(default_factory=list)

    # parameters
    raw_sequence: str | None = None
    sequence: str | None = None
    sequence_length: int | None = None
    fasta_header: str | None = None
    alternatives_requested: bool = False
    alternatives_limit: int | None = None

    def step(self, name: str, outcome: str, detail: str) -> None:
        self.steps.append(
            Step(
                order=len(self.steps) + 1,
                name=name,
                outcome=outcome,
                detail=detail,
                created_at=utc_now(),
            )
        )

    def _duration_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000.0, 3)

    def _base_row(self, status: str) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "created_at": self.started_at_iso,
            "finished_at": utc_now(),
            "status": status,
            "source": self.source,
            "raw_sequence": self.raw_sequence,
            "sequence": self.sequence,
            "sequence_length": self.sequence_length,
            "fasta_header": self.fasta_header,
            "min_loop_length": MIN_LOOP_LENGTH,
            "pseudoknots_supported": 1 if PSEUDOKNOTS_SUPPORTED else 0,
            "allowed_pairs": json.dumps([f"{a}-{b}" for a, b in ALLOWED_PAIR_TUPLES]),
            "alternatives_requested": 1 if self.alternatives_requested else 0,
            "alternatives_limit": self.alternatives_limit,
            "algorithm_name": ALGORITHM_NAME,
            "algorithm_version": ALGORITHM_VERSION,
            "model_scope": MODEL_SCOPE,
            "processing_location": socket.gethostname(),
            "duration_ms": self._duration_ms(),
        }

    def succeed(self, result: FoldResult) -> None:
        row = self._base_row("success")
        row.update(
            optimum=result.optimum,
            primary_dot_bracket=result.primary.dot_bracket,
            primary_pair_count=result.primary.pair_count,
            error_category=None,
            error_message=None,
        )
        all_structures = (result.primary,) + result.alternatives
        with self.db.transaction() as db:
            db.execute(
                _INSERT_REQUEST,
                tuple(row[key] for key in _REQUEST_COLUMNS),
            )
            self._insert_steps(db)
            for order, structure in enumerate(all_structures, start=1):
                db.execute(
                    _INSERT_STRUCTURE,
                    (
                        self.request_id,
                        order,
                        1 if order == 1 else 0,
                        structure.pair_count,
                        structure.dot_bracket,
                        json.dumps([list(p) for p in structure.pairs]),
                        json.dumps(list(structure.pair_table)),
                    ),
                )
            for order, warning in enumerate(result.warnings, start=1):
                db.execute(
                    _INSERT_WARNING,
                    (self.request_id, order, warning),
                )

    def fail(self, category: str, message: str) -> None:
        row = self._base_row("failed")
        row.update(
            optimum=None,
            primary_dot_bracket=None,
            primary_pair_count=None,
            error_category=category,
            error_message=message,
        )
        with self.db.transaction() as db:
            db.execute(
                _INSERT_REQUEST,
                tuple(row[key] for key in _REQUEST_COLUMNS),
            )
            self._insert_steps(db)

    def _insert_steps(self, db: Database) -> None:
        for step in self.steps:
            db.execute(
                _INSERT_STEP,
                (
                    self.request_id,
                    step.order,
                    step.name,
                    step.outcome,
                    step.detail,
                    step.created_at,
                ),
            )


_REQUEST_COLUMNS = (
    "request_id",
    "created_at",
    "finished_at",
    "status",
    "source",
    "raw_sequence",
    "sequence",
    "sequence_length",
    "fasta_header",
    "min_loop_length",
    "pseudoknots_supported",
    "allowed_pairs",
    "alternatives_requested",
    "alternatives_limit",
    "optimum",
    "primary_dot_bracket",
    "primary_pair_count",
    "error_category",
    "error_message",
    "algorithm_name",
    "algorithm_version",
    "model_scope",
    "processing_location",
    "duration_ms",
)

_INSERT_REQUEST = f"""
INSERT INTO requests ({", ".join(_REQUEST_COLUMNS)})
VALUES ({", ".join("?" for _ in _REQUEST_COLUMNS)})
"""

_INSERT_STEP = """
INSERT INTO processing_steps
    (request_id, step_order, step_name, outcome, detail, created_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_STRUCTURE = """
INSERT INTO structures
    (request_id, structure_order, is_primary, pair_count,
     dot_bracket, pairs_json, pair_table_json)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_WARNING = """
INSERT INTO request_warnings (request_id, warning_order, warning)
VALUES (?, ?, ?)
"""


def get_request(db: Database, request_id: str) -> dict[str, Any]:
    """Reconstruct a stored request record (raises ResultNotFoundError)."""
    row = db.execute(
        "SELECT * FROM requests WHERE request_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        raise ResultNotFoundError(f"no stored result for request_id={request_id!r}")

    record = dict(row)
    record["allowed_pairs"] = json.loads(record["allowed_pairs"])

    steps = db.execute(
        "SELECT step_order, step_name, outcome, detail, created_at "
        "FROM processing_steps WHERE request_id = ? ORDER BY step_order",
        (request_id,),
    ).fetchall()
    record["steps"] = [dict(s) for s in steps]

    structures = db.execute(
        "SELECT structure_order, is_primary, pair_count, dot_bracket, "
        "pairs_json, pair_table_json FROM structures "
        "WHERE request_id = ? ORDER BY structure_order",
        (request_id,),
    ).fetchall()
    parsed_structures = []
    for s in structures:
        item = dict(s)
        item["is_primary"] = bool(item["is_primary"])
        item["pairs"] = [tuple(p) for p in json.loads(item.pop("pairs_json"))]
        item["pair_table"] = json.loads(item.pop("pair_table_json"))
        parsed_structures.append(item)
    record["structures"] = parsed_structures

    warnings = db.execute(
        "SELECT warning_order, warning FROM request_warnings "
        "WHERE request_id = ? ORDER BY warning_order",
        (request_id,),
    ).fetchall()
    record["warnings"] = [w["warning"] for w in warnings]
    return record
