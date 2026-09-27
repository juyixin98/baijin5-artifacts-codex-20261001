"""Orchestration: ingest -> validate -> normalize -> block -> score ->
validate constraints -> cluster -> persist run -> diff affected entities.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import Settings
from .corpus.normalize import Normalizer
from .corpus.schema import require_known_ids, validate_batch
from .errors import ComputationError
from .index.blocking import generate_candidates
from .index.store import Store
from .kernel.clustering import ScoredPair, cluster_records
from .kernel.constraints import validate_constraints
from .kernel.similarity import NormalizedRecord, pair_score
from .models import (
    Cluster,
    ConstraintSet,
    Decision,
    Lock,
    PairDecision,
    Record,
    ResolveResult,
)


class ResolutionService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        token_map: Mapping[str, str] | None = None,
    ) -> None:
        settings.validate()
        self.store = store
        self.settings = settings
        self.normalizer = Normalizer(token_map)

    # -- ingest ----------------------------------------------------------
    def ingest(self, records: list[Record]) -> dict[str, Any]:
        validate_batch(records, max_records=self.settings.max_records)
        self.store.upsert_records(records)
        return {"ingested": len(records)}

    # -- constraints / locks ---------------------------------------------
    def set_constraints(self, constraints: ConstraintSet) -> dict[str, Any]:
        known = {r.record_id for r in self.store.load_records()}
        validate_constraints(constraints, known)
        self.store.replace_constraints(constraints)
        return {
            "must_link": len(constraints.must_link),
            "cannot_link": len(constraints.cannot_link),
        }

    def add_lock(self, lock: Lock) -> dict[str, Any]:
        known = {r.record_id for r in self.store.load_records()}
        require_known_ids(lock.record_ids, known, context="lock")
        self.store.put_lock(lock)
        return {"lock_id": lock.lock_id, "records": len(lock.record_ids)}

    # -- resolution ------------------------------------------------------
    def _normalize_all(self, records: list[Record]) -> list[NormalizedRecord]:
        out = []
        for rec in records:
            out.append(
                NormalizedRecord(
                    record_id=rec.record_id,
                    name=self.normalizer.normalize(rec.name),
                    alias_names=tuple(
                        self.normalizer.normalize(a) for a in rec.aliases
                    ),
                    attributes=dict(rec.attributes),
                )
            )
        return out

    def _run_pipeline(
        self,
        records: list[Record],
        constraints: ConstraintSet,
        locks: list[Lock],
    ) -> tuple[dict[str, str], list[PairDecision]]:
        normalized = self._normalize_all(records)
        candidates = generate_candidates(normalized, self.settings)
        by_id = {r.record_id: r for r in normalized}
        scored = [
            ScoredPair(
                left=a,
                right=b,
                breakdown=pair_score(by_id[a], by_id[b], self.settings),
            )
            for a, b in candidates
        ]
        lock_of = {rid: lock.lock_id for lock in locks for rid in lock.record_ids}
        result = cluster_records(
            normalized, scored, constraints, lock_of, self.settings
        )
        return result.assignment, result.decisions

    def resolve(self) -> ResolveResult:
        records = self.store.load_records()
        constraints = self.store.load_constraints()
        locks = self.store.load_locks()

        assignment, decisions = self._run_pipeline(records, constraints, locks)

        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        snapshot = {
            "records": [json.loads(r.model_dump_json()) for r in records],
            "constraints": constraints.model_dump(),
            "locks": [l.model_dump() for l in locks],
        }
        config = {
            "merge_threshold": self.settings.merge_threshold,
            "max_records": self.settings.max_records,
            "max_candidates": self.settings.max_candidates,
            "blocking_df_cap": self.settings.blocking_df_cap,
        }
        digest = hashlib.sha256(
            json.dumps({"snapshot": snapshot, "config": config},
                       sort_keys=True).encode()
        ).hexdigest()[:12]
        run_id = f"run-{now:%Y%m%dT%H%M%S%fZ}-{digest}"

        self.store.create_run(run_id, created_at, config, snapshot)
        self.store.save_decisions(
            run_id, [d.model_dump(mode="json") for d in decisions]
        )
        self.store.save_assignment(run_id, assignment)

        affected = self._affected(run_id, assignment)
        clusters = self._clusters_with_locks(assignment, locks)
        evidence = self._evidence(clusters, decisions)
        return ResolveResult(
            run_id=run_id,
            clusters=clusters,
            decisions=decisions,
            affected_record_ids=affected,
            evidence=evidence,
            meta={"created_at": created_at, "config": config},
        )

    def _affected(self, run_id: str, assignment: dict[str, str]) -> list[str]:
        prev_id = self.store.previous_run_id(run_id)
        if prev_id is None:
            return sorted(assignment)
        prev = self.store.load_assignment(prev_id)

        def members_of(a: dict[str, str]) -> dict[str, frozenset[str]]:
            out: dict[str, frozenset[str]] = {}
            for rid, cid in a.items():
                out[rid] = frozenset(r for r, c in a.items() if c == cid)
            return out

        before, after = members_of(prev), members_of(assignment)
        changed = {
            rid
            for rid in set(before) | set(after)
            if before.get(rid) != after.get(rid)
        }
        return sorted(changed)

    @staticmethod
    def _clusters_with_locks(
        assignment: dict[str, str], locks: list[Lock]
    ) -> list[Cluster]:
        lock_of = {rid: l.lock_id for l in locks for rid in l.record_ids}
        groups: dict[str, list[str]] = {}
        for rid, cid in assignment.items():
            groups.setdefault(cid, []).append(rid)
        clusters = []
        for idx, (cid, members) in enumerate(sorted(groups.items()), 1):
            clusters.append(
                Cluster(
                    cluster_id=f"C{idx}",
                    record_ids=sorted(members),
                    lock_ids=sorted({lock_of[m] for m in members if m in lock_of}),
                )
            )
        return clusters

    @staticmethod
    def _evidence(
        clusters: list[Cluster], decisions: list[PairDecision]
    ) -> dict[str, list[str]]:
        evidence: dict[str, list[str]] = {}
        for cluster in clusters:
            members = set(cluster.record_ids)
            lines = []
            for d in decisions:
                if d.left in members and d.right in members:
                    if d.decision in (Decision.MERGED, Decision.SEEDED):
                        score = "-" if d.score is None else f"{d.score:.3f}"
                        lines.append(
                            f"{d.left} ~ {d.right}: {d.decision.value}"
                            f" (score={score}, reason={d.reason})"
                        )
            for lock_id in cluster.lock_ids:
                lines.append(f"human-confirmed lock: {lock_id}")
            evidence[cluster.cluster_id] = lines
        return evidence

    # -- replay ----------------------------------------------------------
    def replay(self, run_id: str) -> dict[str, Any]:
        """Re-execute a stored run from its snapshot and verify the recorded
        assignment is reproduced exactly."""
        run = self.store.load_run(run_id)
        snap = run["snapshot"]
        records = [Record.model_validate(r) for r in snap["records"]]
        constraints = ConstraintSet.model_validate(snap["constraints"])
        locks = [Lock.model_validate(l) for l in snap["locks"]]
        assignment, _ = self._run_pipeline(records, constraints, locks)
        recorded = run["assignment"]
        if assignment != recorded:
            raise ComputationError(
                "replay mismatch: recomputed assignment differs from journal",
                details={"run_id": run_id},
            )
        return {"run_id": run_id, "replayed": True, "clusters": len(set(recorded.values()))}
