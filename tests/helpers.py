"""Test helpers: drive the kernel pipeline directly (mirrors the service
wiring) and convert results into comparable partition forms."""

from __future__ import annotations

from er_backend.config import Settings
from er_backend.corpus.normalize import Normalizer
from er_backend.index.blocking import generate_candidates
from er_backend.kernel.clustering import ScoredPair, cluster_records
from er_backend.kernel.similarity import NormalizedRecord, pair_score
from er_backend.models import ConstraintSet, Record


def normalize_records(
    records: list[Record], token_map: dict[str, str] | None = None
) -> list[NormalizedRecord]:
    normalizer = Normalizer(token_map)
    return [
        NormalizedRecord(
            record_id=r.record_id,
            name=normalizer.normalize(r.name),
            alias_names=tuple(normalizer.normalize(a) for a in r.aliases),
            attributes=dict(r.attributes),
        )
        for r in records
    ]


def run_kernel(
    records: list[Record],
    constraints: ConstraintSet | None = None,
    locks: dict[str, str] | None = None,
    token_map: dict[str, str] | None = None,
    settings: Settings | None = None,
):
    settings = settings or Settings()
    normalized = normalize_records(records, token_map)
    candidates = generate_candidates(normalized, settings)
    by_id = {r.record_id: r for r in normalized}
    scored = [
        ScoredPair(left=a, right=b,
                   breakdown=pair_score(by_id[a], by_id[b], settings))
        for a, b in candidates
    ]
    return cluster_records(
        normalized, scored, constraints or ConstraintSet(), locks or {}, settings
    )


def partition_of(assignment: dict[str, str]) -> frozenset[frozenset[str]]:
    groups: dict[str, set[str]] = {}
    for rid, cid in assignment.items():
        groups.setdefault(cid, set()).add(rid)
    return frozenset(frozenset(m) for m in groups.values())
