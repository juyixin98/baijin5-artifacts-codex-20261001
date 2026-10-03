"""SQLite-backed minimizer seed index and candidate position query.

A *candidate* is a (sequence, offset) cluster supported by shared minimizer
hashes. It is a recall device for downstream alignment, **not** an
alignment conclusion — every query result is stamped
``conclusion="candidate_only"``.

Storage notes:
- hashes are uint64; SQLite integers are signed, so values >= 2^63 are
  stored offset by 2^64 and converted back on read;
- the low-complexity burst cap is applied at build time: any hash with
  more than ``config.max_hash_occurrences`` rows across the whole build is
  deleted and counted in build stats (``filtered_hashes``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import MinimizerConfig
from .errors import IndexNotFoundError, IndexStateError
from .minimizer import compute_minimizers
from .provenance import RunInfo, fingerprint_sequences
from .sequence import SequenceRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS minimizers (
    hash INTEGER NOT NULL,
    seq_name TEXT NOT NULL,
    position INTEGER NOT NULL,
    strand TEXT NOT NULL CHECK (strand IN ('+', '-'))
);
CREATE INDEX IF NOT EXISTS idx_minimizers_hash ON minimizers(hash);
"""

_UINT64_OFFSET = 1 << 64
_INT64_SIGN = 1 << 63


def _hash_to_db(value: int) -> int:
    return value - _UINT64_OFFSET if value >= _INT64_SIGN else value


def _hash_from_db(value: int) -> int:
    return value + _UINT64_OFFSET if value < 0 else value


@dataclass(frozen=True)
class Candidate:
    """One candidate location cluster. Not an alignment."""

    seq_name: str
    relation: str  # "same" | "swapped" (query strand vs stored strand)
    offset: int
    hits: int
    estimated_ref_start: int

    def to_dict(self) -> dict:
        return {
            "seq_name": self.seq_name,
            "relation": self.relation,
            "offset": self.offset,
            "hits": self.hits,
            "estimated_ref_start": self.estimated_ref_start,
        }


@dataclass
class QueryResult:
    query_name: str
    query_length: int
    query_minimizers: int
    db_hits: int
    candidates: list[Candidate] = field(default_factory=list)
    conclusion: str = "candidate_only"

    def to_dict(self) -> dict:
        return {
            "query_name": self.query_name,
            "query_length": self.query_length,
            "query_minimizers": self.query_minimizers,
            "db_hits": self.db_hits,
            "candidates": [c.to_dict() for c in self.candidates],
            "conclusion": self.conclusion,
        }


class MinimizerIndex:
    """A single SQLite file holding minimizer seeds plus build provenance."""

    def __init__(self, path: Path, config: MinimizerConfig):
        self.path = Path(path)
        self.config = config
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # construction / loading
    # ------------------------------------------------------------------
    @classmethod
    def build(
        cls,
        path: Path,
        records: list[SequenceRecord],
        config: MinimizerConfig,
    ) -> tuple["MinimizerIndex", RunInfo, dict]:
        """Build a fresh index file; returns (index, run_info, stats)."""
        path = Path(path)
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)

        run = RunInfo(
            kind="build",
            config_fingerprint=config.fingerprint(),
            input_fingerprint=fingerprint_sequences(
                [(r.name, r.sequence) for r in records]
            ),
        )
        index = cls(path, config)
        stats: dict = {
            "sequences_total": len(records),
            "sequences_indexed": 0,
            "sequences_skipped_too_short": [],
            "seeds_before_cap": 0,
            "filtered_hashes": 0,
            "filtered_occurrences": 0,
            "seeds_after_cap": 0,
        }
        rows = index._collect_seed_rows(records, run, stats)
        stats["seeds_before_cap"] = len(rows)
        index._conn.executemany(
            "INSERT INTO minimizers(hash, seq_name, position, strand)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        index._apply_occurrence_cap(rows, run, stats)
        stats["seeds_after_cap"] = (
            stats["seeds_before_cap"] - stats["filtered_occurrences"]
        )

        index._write_meta("config", config.fingerprint())
        index._write_meta("run", json.dumps(run.to_dict(), sort_keys=True))
        index._write_meta("stats", json.dumps(stats, sort_keys=True))
        index._conn.commit()
        run.log_step("build_done", **stats)
        return index, run, stats

    def _collect_seed_rows(
        self, records: list[SequenceRecord], run: RunInfo, stats: dict
    ) -> list[tuple[int, str, int, str]]:
        """Compute emitted seeds per record; too-short records are skipped
        and logged, not treated as build failures."""
        rows: list[tuple[int, str, int, str]] = []
        for record in records:
            if len(record) < self.config.min_sequence_length:
                stats["sequences_skipped_too_short"].append(record.name)
                run.log_step(
                    "skip_sequence",
                    name=record.name,
                    reason="SEQUENCE_TOO_SHORT",
                    length=len(record),
                )
                continue
            seeds = compute_minimizers(record.sequence, self.config)
            for seed in seeds:
                rows.append(
                    (_hash_to_db(seed.hash), record.name, seed.position, seed.strand)
                )
            stats["sequences_indexed"] += 1
            run.log_step(
                "index_sequence",
                name=record.name,
                length=len(record),
                seeds=len(seeds),
            )
        return rows

    def _apply_occurrence_cap(
        self, rows: list[tuple[int, str, int, str]], run: RunInfo, stats: dict
    ) -> None:
        """Low-complexity burst cap: drop hashes occurring more than
        ``max_hash_occurrences`` times across the whole build."""
        occurrence_counts = Counter(row[0] for row in rows)
        capped = [
            h
            for h, n in occurrence_counts.items()
            if n > self.config.max_hash_occurrences
        ]
        for h in capped:
            stats["filtered_hashes"] += 1
            stats["filtered_occurrences"] += occurrence_counts[h]
        if capped:
            self._conn.executemany(
                "DELETE FROM minimizers WHERE hash = ?", [(h,) for h in capped]
            )
            run.log_step(
                "apply_occurrence_cap",
                cap=self.config.max_hash_occurrences,
                filtered_hashes=stats["filtered_hashes"],
                filtered_occurrences=stats["filtered_occurrences"],
            )

    @classmethod
    def load(cls, path: Path) -> "MinimizerIndex":
        path = Path(path)
        if not path.exists():
            raise IndexNotFoundError(
                f"index file {path} does not exist",
                detail={"path": str(path)},
            )
        try:
            conn = sqlite3.connect(str(path))
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'config'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise IndexStateError(
                f"index file {path} is not a readable index: {exc}",
                detail={"path": str(path)},
            ) from exc
        finally:
            conn.close()
        if row is None:
            raise IndexStateError(
                f"index file {path} has no config metadata",
                detail={"path": str(path)},
            )
        config = MinimizerConfig.from_dict(json.loads(row[0]))
        return cls(path, config)

    # ------------------------------------------------------------------
    # metadata
    # ------------------------------------------------------------------
    def _write_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value)
        )

    def read_meta(self, key: str) -> dict:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise IndexStateError(
                f"index {self.path} is missing meta key {key!r}",
                detail={"path": str(self.path), "key": key},
            )
        return json.loads(row[0])

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------
    def query(self, read: SequenceRecord, run: RunInfo | None = None) -> QueryResult:
        """Find candidate positions for one read.

        Raises :class:`SequenceTooShortError` for reads with no full
        window; the caller maps that to the READ_TOO_SHORT failure
        category rather than returning an empty success.
        """
        run = run or RunInfo(
            kind="query", config_fingerprint=self.config.fingerprint()
        )
        seeds = compute_minimizers(read.sequence, self.config)
        run.log_step(
            "seed_query",
            query=read.name,
            length=len(read),
            query_minimizers=len(seeds),
        )

        placeholders = ",".join("?" for _ in seeds) or "NULL"
        rows = self._conn.execute(
            f"SELECT hash, seq_name, position, strand FROM minimizers"
            f" WHERE hash IN ({placeholders})",
            [_hash_to_db(s.hash) for s in seeds],
        ).fetchall()
        run.log_step("lookup", db_hits=len(rows))
        candidates = self._cluster_hits(seeds, rows, read_length=len(read))
        run.log_step(
            "cluster",
            clusters=len(candidates),
            top_hits=candidates[0].hits if candidates else 0,
        )
        return QueryResult(
            query_name=read.name,
            query_length=len(read),
            query_minimizers=len(seeds),
            db_hits=len(rows),
            candidates=candidates,
        )

    def _cluster_hits(self, seeds, rows, read_length: int) -> list[Candidate]:
        """Group shared-hash hits into (sequence, relation, offset)
        clusters. Same-strand anchors line up at ``ref_pos - qpos``;
        swapped-strand anchors (reverse-complemented read) line up at
        ``ref_pos + qpos``. See README "Candidate semantics"."""
        by_hash: dict[int, list] = {}
        for seed in seeds:
            by_hash.setdefault(seed.hash, []).append(seed)
        clusters: Counter = Counter()
        for db_hash, seq_name, ref_pos, ref_strand in rows:
            for qseed in by_hash.get(_hash_from_db(db_hash), ()):
                if qseed.strand == ref_strand:
                    key = (seq_name, "same", ref_pos - qseed.position)
                else:
                    key = (seq_name, "swapped", ref_pos + qseed.position)
                clusters[key] += 1
        candidates = [
            Candidate(
                seq_name=seq_name,
                relation=relation,
                offset=offset,
                hits=hits,
                estimated_ref_start=(
                    offset
                    if relation == "same"
                    else offset + self.config.k - read_length
                ),
            )
            for (seq_name, relation, offset), hits in clusters.items()
        ]
        candidates.sort(key=lambda c: (-c.hits, c.seq_name, c.relation, c.offset))
        return candidates

    def close(self) -> None:
        self._conn.close()


def new_index_path(directory: Path) -> tuple[str, Path]:
    index_id = uuid.uuid4().hex[:12]
    return index_id, Path(directory) / f"index_{index_id}.db"
