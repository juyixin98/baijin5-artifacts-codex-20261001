"""SQLite persistence: transcript catalog + provenance log.

The store is the single source of truth for the service layer. Transcripts
are loaded once from local fixture files; provenance rows are appended per
request and are queryable by request id.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List

from .models import Contig, Exon, Strand, Transcript
from .provenance import ProvenanceRecord
from .reference import parse_fasta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    tx_id   TEXT PRIMARY KEY,
    gene    TEXT NOT NULL,
    contig  TEXT NOT NULL,
    strand  TEXT NOT NULL CHECK (strand IN ('+', '-')),
    exons   TEXT NOT NULL  -- JSON array of [start, end) pairs, genomic ascending
);
CREATE TABLE IF NOT EXISTS provenance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    transcript_id   TEXT,
    status          TEXT NOT NULL,
    reason          TEXT NOT NULL,
    input_redacted  TEXT NOT NULL,
    output_redacted TEXT NOT NULL,
    input_sha256    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provenance_request ON provenance(request_id);
"""


class Store:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- transcript catalog -------------------------------------------------

    def load_transcripts(self, transcripts: List[Transcript]) -> int:
        """Replace the transcript catalog. Returns rows written."""
        with self._conn:
            self._conn.execute("DELETE FROM transcripts")
            self._conn.executemany(
                "INSERT INTO transcripts (tx_id, gene, contig, strand, exons)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        tx.tx_id,
                        tx.gene,
                        tx.contig,
                        tx.strand.value,
                        json.dumps([[e.start, e.end] for e in tx.exons]),
                    )
                    for tx in transcripts
                ],
            )
        return len(transcripts)

    def get_transcripts(self) -> Dict[str, Transcript]:
        rows = self._conn.execute(
            "SELECT tx_id, gene, contig, strand, exons FROM transcripts"
        ).fetchall()
        result: Dict[str, Transcript] = {}
        for row in rows:
            exons = tuple(
                Exon(start=int(s), end=int(e)) for s, e in json.loads(row["exons"])
            )
            tx = Transcript(
                tx_id=row["tx_id"],
                gene=row["gene"],
                contig=row["contig"],
                strand=Strand.parse(row["strand"]),
                exons=exons,
            )
            result[tx.tx_id] = tx
        return result

    # -- provenance ----------------------------------------------------------

    def record_provenance(self, record: ProvenanceRecord) -> None:
        row = record.as_row()
        with self._conn:
            self._conn.execute(
                "INSERT INTO provenance (request_id, endpoint, transcript_id,"
                " status, reason, input_redacted, output_redacted,"
                " input_sha256, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["request_id"],
                    row["endpoint"],
                    row["transcript_id"],
                    row["status"],
                    row["reason"],
                    row["input_redacted"],
                    row["output_redacted"],
                    row["input_sha256"],
                    row["created_at"],
                ),
            )

    def get_provenance(self, request_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT request_id, endpoint, transcript_id, status, reason,"
            " input_redacted, output_redacted, input_sha256, created_at"
            " FROM provenance WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def load_fixture_transcripts(path: Path) -> List[Transcript]:
    """Parse the transcripts JSON fixture into domain objects."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    transcripts: List[Transcript] = []
    for entry in payload["transcripts"]:
        exons = tuple(Exon(start=int(s), end=int(e)) for s, e in entry["exons"])
        transcripts.append(
            Transcript(
                tx_id=entry["id"],
                gene=entry["gene"],
                contig=entry["contig"],
                strand=Strand.parse(entry["strand"]),
                exons=exons,
            )
        )
    return transcripts


def load_fixture_contigs(fasta_path: Path) -> Dict[str, Contig]:
    return parse_fasta(fasta_path)
