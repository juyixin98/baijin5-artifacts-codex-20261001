"""Shared data contracts between modules (corpus / kernel / index / api)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Corpus contract
# ---------------------------------------------------------------------------

class Record(BaseModel):
    """One organization-name record as ingested into the corpus."""

    record_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=512)
    aliases: list[str] = Field(default_factory=list)
    locale: str | None = None
    # Free-form attributes; "registration_id" is treated as a strong
    # identifier by the similarity kernel when present on both records.
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("record_id", "name")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty after stripping")
        return v


class ConstraintSet(BaseModel):
    """Pairwise must-link / cannot-link constraints over record ids."""

    must_link: list[tuple[str, str]] = Field(default_factory=list)
    cannot_link: list[tuple[str, str]] = Field(default_factory=list)


class Lock(BaseModel):
    """A human-confirmed mapping. Locked records must remain clustered
    together; two clusters carrying different lock ids may never merge."""

    lock_id: str = Field(min_length=1)
    record_ids: list[str] = Field(min_length=1)
    note: str = ""


# ---------------------------------------------------------------------------
# Kernel output contract
# ---------------------------------------------------------------------------

class Decision(str, Enum):
    MERGED = "merged"
    REJECTED = "rejected"
    SEEDED = "seeded"  # merged by must-link / lock before scoring


class PairDecision(BaseModel):
    """One audited step of the clustering run."""

    left: str
    right: str
    score: float | None
    decision: Decision
    reason: str


class Cluster(BaseModel):
    cluster_id: str
    record_ids: list[str]
    lock_ids: list[str] = Field(default_factory=list)


class ResolveResult(BaseModel):
    run_id: str
    clusters: list[Cluster]
    decisions: list[PairDecision]
    # Record ids whose cluster membership changed vs. the previous run.
    affected_record_ids: list[str]
    # cluster_id -> human-readable evidence lines.
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
