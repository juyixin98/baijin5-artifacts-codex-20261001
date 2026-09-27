"""Candidate generation (blocking) over normalized records.

Pairs are generated from a token inverted index plus exact-normalized-name
buckets. High document-frequency tokens are skipped to bound the candidate
set; exceeding the configured budget raises ResourceExhaustedError so the
caller sees a distinct failure class instead of a silent slowdown.
"""

from __future__ import annotations

from collections import defaultdict

from ..config import Settings
from ..errors import ResourceExhaustedError
from ..kernel.similarity import NormalizedRecord


def generate_candidates(
    records: list[NormalizedRecord], settings: Settings
) -> list[tuple[str, str]]:
    if len(records) > settings.max_records:
        raise ResourceExhaustedError(
            f"{len(records)} records exceeds max_records={settings.max_records}",
            details={"records": len(records), "max_records": settings.max_records},
        )

    token_index: dict[str, list[str]] = defaultdict(list)
    name_index: dict[str, list[str]] = defaultdict(list)
    for rec in records:
        for tok in rec.token_set:
            token_index[tok].append(rec.record_id)
        name_index[rec.name.normalized].append(rec.record_id)

    n = len(records)
    # Skip ultra-common tokens only in larger corpora; for small corpora the
    # absolute floor keeps blocking total (and deterministic).
    df_cap = max(10, int(settings.blocking_df_cap * max(n, 1)) + 1)

    pairs: set[tuple[str, str]] = set()
    for ids in list(token_index.values()) + list(name_index.values()):
        if len(ids) > df_cap:
            continue
        for i, a in enumerate(sorted(ids)):
            for b in sorted(ids)[i + 1 :]:
                pairs.add((a, b))

    ordered = sorted(pairs)
    if len(ordered) > settings.max_candidates:
        raise ResourceExhaustedError(
            f"{len(ordered)} candidate pairs exceeds "
            f"max_candidates={settings.max_candidates}",
            details={
                "candidates": len(ordered),
                "max_candidates": settings.max_candidates,
            },
        )
    return ordered
