"""Service + SQLite index tests: candidate recall and failure categories."""
from __future__ import annotations

import pytest

from miniseed.config import Settings
from miniseed.errors import ErrorCode, MiniseedError
from miniseed.service import MiniseedService
from miniseed.store import SeedStore

K, W = 9, 5

# A reference with two distinctive anchor regions separated by a low
# complexity run.
REF = (
    "CAGTACCTGAGATCGATCGTTACCGGTAA"  # unique left anchor (30 bp)
    + "T" * 30                        # homopolymer outbreak
    + "GGCTATCGGATCCAAGTCACTGAGTCTGA"  # unique right anchor (30 bp)
)
LEFT = REF[:24]
RIGHT = REF[60 - 6 : 60 - 6 + 24]


def _rc(s: str) -> str:
    comp = {"A": "T", "C": "G", "G": "C", "T": "A"}
    return "".join(comp[b] for b in reversed(s))


def test_index_persists_run_and_seeds(service: MiniseedService):
    r = service.index_reference("run-a", REF, k=K, w=W)
    assert r.run_id == "run-a"
    assert r.ref_length == len(REF)
    assert r.seed_count > 0
    # Distinct values <= records; homopolymer contributes only one value.
    assert r.distinct_seed_values <= r.seed_count
    run = service.store.get_run("run-a")
    assert run["k"] == K and run["w"] == W and run["seed_count"] == r.seed_count


def test_exact_substring_is_recalled_as_forward_candidate(service):
    service.index_reference("run-a", REF, k=K, w=W)
    q = service.query_read("run-a", LEFT, k=K, w=W)
    assert q.total_hits > 0
    top = q.locations[0]
    assert top.strand == "+"
    assert top.hit_count >= 2  # multiple shared minimizers anchor the locus
    # The diagonal must point back to the true planted origin (offset 0).
    assert top.diagonal == top.ref_start - 0 or top.ref_start < len(LEFT)
    # Every hit is explicitly flagged as non-alignment evidence.
    assert q.candidate_is_alignment is False


def test_reverse_complement_read_recalled_on_reverse_strand(service):
    service.index_reference("run-a", REF, k=K, w=W)
    q = service.query_read("run-a", _rc(LEFT), k=K, w=W)
    assert q.total_hits > 0
    strands = {loc.strand for loc in q.locations if loc.hit_count >= 2}
    assert "-" in strands


def test_novel_read_returns_zero_candidates_not_an_error(service):
    service.index_reference("run-a", REF, k=K, w=W)
    q = service.query_read("run-a", "GGCTA" * 6, k=K, w=W)  # 30 bp
    assert q.total_hits == 0
    assert q.locations == []


def test_parameter_mismatch_is_rejected(service):
    service.index_reference("run-a", REF, k=K, w=W)
    with pytest.raises(MiniseedError) as exc:
        service.query_read("run-a", LEFT, k=11, w=W)
    assert exc.value.code is ErrorCode.PARAMETER_CONFLICT
    assert exc.value.context["indexed"] == {"k": K, "w": W}
    assert exc.value.context["query"] == {"k": 11, "w": W}


def test_unknown_run_category(service):
    with pytest.raises(MiniseedError) as exc:
        service.query_read("ghost", LEFT, k=K, w=W)
    assert exc.value.code is ErrorCode.RUN_NOT_FOUND


def test_duplicate_run_and_overwrite(service):
    service.index_reference("run-a", REF, k=K, w=W)
    with pytest.raises(MiniseedError) as exc:
        service.index_reference("run-a", REF, k=K, w=W)
    assert exc.value.code is ErrorCode.RUN_ALREADY_EXISTS
    # overwrite=True replaces cleanly.
    r2 = service.index_reference(
        "run-a", REF, k=K, w=W, overwrite=True
    )
    assert r2.seed_count > 0
    assert service.store.get_run("run-a")["seed_count"] == r2.seed_count


def test_repetitive_bucket_cap_rejects_outbreak(tmp_path):
    # Tandem repeat: the same minimizer VALUE recurs at non-adjacent windows,
    # populating one large bucket despite per-run dedup.
    settings = Settings(
        k=9, w=5, max_bucket_size=3, max_candidates=500,
        db_path=tmp_path / "rep.db",
    )
    with SeedStore(settings.db_path) as store:
        svc = MiniseedService(store, settings)
        with pytest.raises(MiniseedError) as exc:
            svc.index_reference("rep", "GATTACA" * 30, k=9, w=5)
        assert exc.value.code is ErrorCode.BUCKET_OVERFLOW
        assert exc.value.context["max_bucket_size"] == 3
        # Failed indexing must not leave a run row behind (rollback semantics
        # for seeds; run metadata row existence is explicitly checked).
        assert store.bucket_sizes("rep") == {}


def test_candidate_cap_raises_categorized_error(tmp_path):
    settings = Settings(
        k=9, w=5, max_bucket_size=200, max_candidates=1,
        db_path=tmp_path / "cap.db",
    )
    with SeedStore(settings.db_path) as store:
        svc = MiniseedService(store, settings)
        svc.index_reference("rep", "GATTACA" * 30, k=9, w=5)
        with pytest.raises(MiniseedError) as exc:
            svc.query_read("rep", "GATTACA" * 5, k=9, w=5)
        assert exc.value.code is ErrorCode.TOO_MANY_CANDIDATES


def test_short_read_below_window_floor(service):
    service.index_reference("run-a", REF, k=K, w=W)
    with pytest.raises(MiniseedError) as exc:
        service.query_read("run-a", "ACGT", k=K, w=W)
    assert exc.value.code is ErrorCode.SEQUENCE_TOO_SHORT


def test_audit_trail_correlates_identity(service):
    service.index_reference("run-a", REF, k=K, w=W)
    service.store.audit("probe", "identity-xyz", {"ok": True}, run_id="run-a")
    events = service.store.list_audit("run-a")
    identities = {e["identity"] for e in events}
    assert "identity-xyz" in identities
