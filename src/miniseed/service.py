"""Application service: orchestrates parsing, scanning, indexing and query.

This is the only layer the API and the demo script call. It enforces the
parameter-compatibility contract and turns raw seed hits into grouped
candidate locations (NumPy diagonal binning) -- explicitly **candidate**
locations, not alignments.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np

from .config import Settings
from .errors import ErrorCode, MiniseedError
from .hashing import HASH_VERSION
from .minimizer import Minimizer, minimum_length, scan_minimizers, validate_params
from .sequence import parse_sequence
from .store import CandidateHit, SeedStore


@dataclass(frozen=True)
class IndexResult:
    run_id: str
    k: int
    w: int
    hash_version: str
    ref_name: str
    ref_length: int
    seed_count: int
    distinct_seed_values: int
    max_bucket_size: int
    windows: int
    windows_without_kmer: int


@dataclass(frozen=True)
class CandidateLocation:
    """A cluster of seed hits sharing one implied reference anchor.

    For same-strand hits the collinear invariant is
    ``diagonal = ref_offset - query_offset``; for opposite-strand hits
    (reverse-complement mapping) it reflects to
    ``diagonal = ref_offset + query_offset``. Grouping by this strand-aware
    diagonal yields candidate locations. Nothing here asserts a true
    alignment -- seeds can collide by chance.
    """

    ref_start: int
    ref_end: int
    diagonal: int
    strand: str            # "+" same strand, "-" reverse-complement reflected
    strand_consistent: bool
    hit_count: int
    hashes: list[int]


@dataclass(frozen=True)
class QueryResult:
    run_id: str
    k: int
    w: int
    hash_version: str
    query_length: int
    query_seed_count: int
    windows: int
    windows_without_kmer: int
    total_hits: int
    candidate_is_alignment: bool = field(default=False)
    note: str = "candidate hits are shared minimizers, not alignments"
    locations: list[CandidateLocation] = field(default_factory=list)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class MiniseedService:
    def __init__(self, store: SeedStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------- indexing
    def index_reference(
        self,
        run_id: str,
        reference: str,
        *,
        ref_name: str = "reference",
        k: int | None = None,
        w: int | None = None,
        overwrite: bool = False,
    ) -> IndexResult:
        k = self.settings.k if k is None else k
        w = self.settings.w if w is None else w
        validate_params(k, w)
        parsed = parse_sequence(reference, name="reference")
        scan = scan_minimizers(parsed.sequence, k, w)

        self.store.create_run(
            run_id,
            k=k,
            w=w,
            ref_name=ref_name,
            ref_length=parsed.length,
            overwrite=overwrite,
        )
        try:
            seed_count = self.store.bulk_insert_seeds(
                run_id, scan.minimizers, self.settings.max_bucket_size
            )
        except MiniseedError:
            # A rejected (e.g. overflowing) index leaves no partial run.
            self.store.delete_run(run_id)
            raise
        buckets = self.store.bucket_sizes(run_id)
        return IndexResult(
            run_id=run_id,
            k=k,
            w=w,
            hash_version=HASH_VERSION,
            ref_name=ref_name,
            ref_length=parsed.length,
            seed_count=seed_count,
            distinct_seed_values=len(buckets),
            max_bucket_size=max(buckets.values()),
            windows=scan.total_windows,
            windows_without_kmer=scan.windows_without_kmer,
        )

    # -------------------------------------------------------------- queries
    def _check_run_params(self, run_id: str, k: int, w: int):
        run = self.store.get_run(run_id)  # raises RUN_NOT_FOUND
        if run["k"] != k or run["w"] != w:
            raise MiniseedError(
                ErrorCode.PARAMETER_CONFLICT,
                f"run {run_id!r} was indexed with k={run['k']}, w={run['w']} "
                f"but the query used k={k}, w={w}",
                context={
                    "run_id": run_id,
                    "indexed": {"k": run["k"], "w": run["w"]},
                    "query": {"k": k, "w": w},
                },
            )
        return run

    def query_read(
        self,
        run_id: str,
        read: str,
        *,
        k: int | None = None,
        w: int | None = None,
    ) -> QueryResult:
        k = self.settings.k if k is None else k
        w = self.settings.w if w is None else w
        validate_params(k, w)
        self._check_run_params(run_id, k, w)
        parsed = parse_sequence(read, name="read")
        if parsed.length < minimum_length(k, w):
            raise MiniseedError(
                ErrorCode.SEQUENCE_TOO_SHORT,
                f"read length {parsed.length} cannot cover a window "
                f"(need >= k + w - 1 = {minimum_length(k, w)})",
                context={"length": parsed.length, "k": k, "w": w},
            )

        scan = scan_minimizers(parsed.sequence, k, w)
        hits = self.store.lookup_candidates(
            run_id, scan.minimizers, max_candidates=self.settings.max_candidates
        )
        locations = self._group_hits(hits)
        return QueryResult(
            run_id=run_id,
            k=k,
            w=w,
            hash_version=HASH_VERSION,
            query_length=parsed.length,
            query_seed_count=len(scan.minimizers),
            windows=scan.total_windows,
            windows_without_kmer=scan.windows_without_kmer,
            total_hits=len(hits),
            locations=locations,
        )

    @staticmethod
    def _group_hits(hits: list[CandidateHit]) -> list[CandidateLocation]:
        """Group raw hits into candidate locations by strand-aware diagonal.

        Same-orientation hits (ref k-mer and query k-mer lie on the same side
        of the canonical form) are forward mappings keyed by ``ref - query``;
        opposite-orientation hits are reverse-complement mappings keyed by
        ``ref + query``. NumPy performs the vectorized offset arithmetic;
        Python then aggregates ordered, readable groups.
        """
        if not hits:
            return []
        ref = np.asarray([h.ref_offset for h in hits], dtype=np.int64)
        qry = np.asarray([h.query_offset for h in hits], dtype=np.int64)
        same = np.asarray(
            [h.strand_consistent for h in hits], dtype=bool
        )
        # Forward diagonal for same-orientation pairs, reflected diagonal for
        # reverse-complement pairs.
        diag_forward = ref - qry
        diag_reverse = ref + qry
        diagonals = np.where(same, diag_forward, diag_reverse)
        strands = np.where(same, "+", "-")

        groups: dict[tuple[str, int], dict] = {}
        order: list[tuple[str, int]] = []
        for idx, key in enumerate(zip(strands.tolist(), diagonals.tolist())):
            h = hits[idx]
            if key not in groups:
                groups[key] = {
                    "ref_start": h.ref_offset,
                    "ref_end": h.ref_offset,
                    "same_orientation": h.strand_consistent,
                    "count": 0,
                    "hashes": [],
                }
                order.append(key)
            g = groups[key]
            g["ref_start"] = min(g["ref_start"], h.ref_offset)
            g["ref_end"] = max(g["ref_end"], h.ref_offset)
            g["count"] += 1
            g["hashes"].append(h.hash)

        result = [
            CandidateLocation(
                ref_start=groups[key]["ref_start"],
                ref_end=groups[key]["ref_end"],
                diagonal=key[1],
                strand=key[0],
                strand_consistent=groups[key]["same_orientation"],
                hit_count=groups[key]["count"],
                hashes=sorted(set(groups[key]["hashes"])),
            )
            for key in order
        ]
        # Most-supported candidate first, forward strand before reverse.
        result.sort(
            key=lambda loc: (
                0 if loc.strand == "+" else 1,
                -loc.hit_count,
                loc.ref_start,
            )
        )
        return result
