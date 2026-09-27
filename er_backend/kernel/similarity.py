"""Pairwise similarity between normalized records.

Scores are *evidence*, not decisions: a high pairwise score never forces a
merge on its own (constraints and locks are checked by the clustering
stage), and identical names do not imply the same entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..corpus.normalize import NormalizedName


@dataclass(frozen=True)
class NormalizedRecord:
    record_id: str
    name: NormalizedName
    alias_names: tuple[NormalizedName, ...]
    attributes: dict[str, str]

    @property
    def token_set(self) -> frozenset[str]:
        return frozenset(self.name.tokens)


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    features: dict[str, float] = field(default_factory=dict)
    reason: str = "weighted"


def levenshtein_ratio(a: str, b: str) -> float:
    """1 - dist/max(len) with a full DP edit distance (deterministic)."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


def token_jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pair_score(
    a: NormalizedRecord, b: NormalizedRecord, settings: Settings
) -> ScoreBreakdown:
    # Strong shared identifier (e.g. registration id) is decisive evidence.
    id_a = a.attributes.get("registration_id")
    id_b = b.attributes.get("registration_id")
    if id_a and id_b and id_a == id_b:
        return ScoreBreakdown(
            score=settings.shared_id_score,
            features={"shared_registration_id": 1.0},
            reason="shared_registration_id",
        )

    if a.name.normalized == b.name.normalized:
        # Same normalized name is strong but *not* conclusive evidence;
        # a cannot-link constraint can still keep the records apart.
        return ScoreBreakdown(
            score=settings.exact_name_score,
            features={"exact_name": 1.0},
            reason="exact_name",
        )

    aliases_b = {al.normalized for al in b.alias_names}
    aliases_a = {al.normalized for al in a.alias_names}
    if a.name.normalized in aliases_b or b.name.normalized in aliases_a:
        return ScoreBreakdown(
            score=settings.alias_score,
            features={"alias_match": 1.0},
            reason="alias_match",
        )

    jac = token_jaccard(a.token_set, b.token_set)
    edit = levenshtein_ratio(a.name.normalized, b.name.normalized)
    score = settings.weight_jaccard * jac + settings.weight_edit * edit
    return ScoreBreakdown(
        score=round(score, 6),
        features={"token_jaccard": round(jac, 6), "levenshtein": round(edit, 6)},
        reason="weighted",
    )
