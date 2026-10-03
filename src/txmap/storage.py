"""SQLite persistence.

Two kinds of data live here:
  * reference data (chromosomes, transcripts, exons) loaded from the fixture
  * provenance (map_audit) -- one row per mapping attempt, accepted or rejected

All transcript-scoped queries filter by transcript_id, which enforces identity
isolation at the storage boundary.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Exon, Transcript

SCHEMA = """
CREATE TABLE IF NOT EXISTS chromosomes (
    name    TEXT PRIMARY KEY,
    length  INTEGER NOT NULL,
    pattern TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id TEXT PRIMARY KEY,
    chrom         TEXT NOT NULL REFERENCES chromosomes(name),
    strand        TEXT NOT NULL CHECK (strand IN ('+', '-')),
    length        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exons (
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    exon_index    INTEGER NOT NULL,
    gen_start     INTEGER NOT NULL,
    gen_end       INTEGER NOT NULL,
    PRIMARY KEY (transcript_id, exon_index)
);
CREATE TABLE IF NOT EXISTS map_audit (
    audit_id      TEXT PRIMARY KEY,
    request_id    TEXT NOT NULL,
    transcript_id TEXT,
    direction     TEXT NOT NULL,
    mode          TEXT NOT NULL,
    input_json    TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('mapped', 'rejected', 'partial')),
    result_json   TEXT,
    error_code    TEXT,
    error_detail  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_map_audit_request ON map_audit(request_id);
CREATE INDEX IF NOT EXISTS idx_map_audit_tx ON map_audit(transcript_id);
"""


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------- reference loading

    def replace_reference(
        self,
        chromosomes: dict[str, int],
        patterns: dict[str, str],
        transcripts: dict[str, Transcript],
    ) -> int:
        """Idempotent full reload; returns number of transcripts stored."""
        with self._conn:
            self._conn.execute("DELETE FROM exons")
            self._conn.execute("DELETE FROM transcripts")
            self._conn.execute("DELETE FROM chromosomes")
            self._conn.executemany(
                "INSERT INTO chromosomes(name, length, pattern) VALUES (?, ?, ?)",
                [(name, length, patterns[name]) for name, length in chromosomes.items()],
            )
            for tx in transcripts.values():
                self._conn.execute(
                    "INSERT INTO transcripts(transcript_id, chrom, strand, length)"
                    " VALUES (?, ?, ?, ?)",
                    (tx.transcript_id, tx.chrom, tx.strand, tx.length),
                )
                self._conn.executemany(
                    "INSERT INTO exons(transcript_id, exon_index, gen_start, gen_end)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (tx.transcript_id, i, e.start, e.end)
                        for i, e in enumerate(tx.exons)
                    ],
                )
        return len(transcripts)

    # ------------------------------------------------------------------- reads

    def list_transcript_ids(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT transcript_id FROM transcripts ORDER BY transcript_id"
        ).fetchall()
        return [r["transcript_id"] for r in rows]

    def get_transcript(self, transcript_id: str) -> Transcript | None:
        row = self._conn.execute(
            "SELECT transcript_id, chrom, strand FROM transcripts"
            " WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchone()
        if row is None:
            return None
        exons = self._conn.execute(
            "SELECT gen_start, gen_end FROM exons"
            " WHERE transcript_id = ? ORDER BY exon_index",
            (transcript_id,),
        ).fetchall()
        return Transcript(
            transcript_id=row["transcript_id"],
            chrom=row["chrom"],
            strand=row["strand"],
            exons=tuple(Exon(r["gen_start"], r["gen_end"]) for r in exons),
        )

    def get_pattern(self, chrom: str) -> str | None:
        row = self._conn.execute(
            "SELECT pattern FROM chromosomes WHERE name = ?", (chrom,)
        ).fetchone()
        return None if row is None else row["pattern"]

    # ----------------------------------------------------------------- auditing

    def record_audit(self, record: dict[str, object]) -> None:
        self._conn.execute(
            "INSERT INTO map_audit(audit_id, request_id, transcript_id, direction,"
            " mode, input_json, status, result_json, error_code, error_detail,"
            " created_at)"
            " VALUES (:audit_id, :request_id, :transcript_id, :direction, :mode,"
            " :input_json, :status, :result_json, :error_code, :error_detail,"
            " :created_at)",
            {
                "audit_id": record["audit_id"],
                "request_id": record["request_id"],
                "transcript_id": record.get("transcript_id"),
                "direction": record["direction"],
                "mode": record["mode"],
                "input_json": json.dumps(record["input"], sort_keys=True),
                "status": record["status"],
                "result_json": (
                    json.dumps(record["result"], sort_keys=True)
                    if record.get("result") is not None
                    else None
                ),
                "error_code": record.get("error_code"),
                "error_detail": record.get("error_detail"),
                "created_at": record["created_at"],
            },
        )
        self._conn.commit()

    def get_audit(self, audit_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM map_audit WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_json"] = json.loads(d["input_json"])
        d["result_json"] = json.loads(d["result_json"]) if d["result_json"] else None
        return d

    def list_audit_by_request(self, request_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT * FROM map_audit WHERE request_id = ? ORDER BY created_at",
            (request_id,),
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["input_json"] = json.loads(d["input_json"])
            d["result_json"] = json.loads(d["result_json"]) if d["result_json"] else None
            out.append(d)
        return out
