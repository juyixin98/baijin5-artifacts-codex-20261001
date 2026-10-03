"""Orchestration: stream -> (external sort) -> adjudicate -> depth -> provenance.

The engine never trusts input ordering and never holds more than one chunk
of records in memory when external sorting is enabled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Iterator

from .config import Settings
from .coverage import (
    UNION_PER_QUERY,
    coverage_for_reference,
)
from .diagnostics import (
    Timer,
    configure_logging,
    malformed_event,
    verdict_event,
)
from .external_sort import external_sort
from .filtering import FilterConfig, adjudicate
from .models import Alignment, DepthResult, RejectReason, Verdict
from .provenance import ProvenanceStore, RunRecord, utc_now

#: Failures that mean "we cannot judge the record" rather than "bad biology".
UNDETERMINED_REASONS = frozenset({RejectReason.MALFORMED_RECORD.value})


@dataclass(frozen=True)
class Reference:
    name: str
    length: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("reference name must be non-empty")
        if self.length <= 0:
            raise ValueError(
                f"reference {self.name!r} length must be > 0, got {self.length}"
            )


@dataclass
class RunReport:
    run_id: str
    request_id: str
    results: dict[str, DepthResult]
    verdicts: list[Verdict]
    diagnostics: list[dict]
    input_count: int
    references: dict[str, int]
    settings: Settings
    external_sorted: bool

    @property
    def accepted(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.accepted]

    @property
    def rejected(self) -> list[Verdict]:
        return [
            v for v in self.verdicts
            if not v.accepted and v.reason not in UNDETERMINED_REASONS
        ]

    @property
    def undetermined(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.reason in UNDETERMINED_REASONS]

    def conservation(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "ref_length": result.ref_length,
                "weighted_length": result.weighted_length,
                "covered_bases": result.covered_bases,
                "histogram_bases": sum(result.histogram.values()),
                "segment_count": len(result.segments),
            }
            for name, result in self.results.items()
        }


def _undetermined_verdict(index: int, detail: str) -> Verdict:
    return Verdict(
        query_name=f"row#{index}",
        accepted=False,
        reason=RejectReason.MALFORMED_RECORD.value,
        detail=detail,
    )


def analyze(
    records: Iterable[Alignment],
    references: Iterable[Reference],
    settings: Settings | None = None,
    *,
    request_id: str | None = None,
    store: ProvenanceStore | None = None,
    use_external_sort: bool = False,
    tmp_dir: str | None = None,
) -> RunReport:
    """Run the full pipeline over ``records``.

    Malformed records cannot occur for typed :class:`Alignment` input; use
    :func:`analyze_lines` for raw TSV rows where parse failures are recorded
    as undetermined.
    """
    settings = settings or Settings()
    request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
    run_id = f"run-{uuid.uuid4().hex[:16]}"
    logger = configure_logging()

    ref_map = {r.name: r.length for r in references}
    if not ref_map:
        raise ValueError("at least one reference is required")
    known_refs = set(ref_map)

    filter_config = FilterConfig(
        min_mapq=settings.min_mapq,
        reject_flagged_duplicates=settings.reject_flagged_duplicates,
    )

    stream: Iterator[Alignment] = iter(records)
    if use_external_sort:
        stream = external_sort(
            stream,
            chunk_size=settings.external_sort_chunk_size,
            tmp_dir=tmp_dir,
        )

    verdicts: list[Verdict] = []
    raw_cigars: dict[int, str] = {}
    events: list[dict] = []
    accepted_by_ref: dict[str, list[Verdict]] = {}
    # Exact-record de-duplication (union policy only). Same-qname records
    # with DIFFERENT blocks are kept and later union-merged; only an exact
    # repeat of (qname, ref, blocks) is rejected, so repeated input rows can
    # never silently double count.
    seen_record_keys: set[tuple[str, str, tuple[tuple[int, int], ...]]] = set()

    for index, alignment in enumerate(stream):
        timer = Timer()
        ref_len = ref_map.get(alignment.ref_name)
        verdict = adjudicate(
            alignment,
            filter_config,
            ref_len,
            known_refs=known_refs,
        )
        if verdict.accepted and settings.dedup_policy == UNION_PER_QUERY:
            dedup_key = (
                verdict.query_name,
                verdict.ref_name,
                tuple((b.start, b.end) for b in verdict.blocks),
            )
            if dedup_key in seen_record_keys:
                verdict = Verdict(
                    query_name=verdict.query_name,
                    ref_name=verdict.ref_name,
                    ref_start=verdict.ref_start,
                    ref_end=verdict.ref_end,
                    mapq=verdict.mapq,
                    accepted=False,
                    reason=RejectReason.DUPLICATE.value,
                    detail=(
                        "exact duplicate record (same query_name and covered "
                        "blocks) already seen; counted once under "
                        "union_per_query. Overlapping mates with distinct "
                        "blocks are union-merged, not rejected."
                    ),
                )
            else:
                seen_record_keys.add(dedup_key)
        raw_cigars[index] = alignment.cigar
        verdicts.append(verdict)
        event = verdict_event(
            verdict,
            request_id=request_id,
            elapsed_ms=timer.elapsed_ms(),
        )
        events.append(event.as_dict())
        logger.info(
            "%s %s record=%s ref=%s reason=%s",
            request_id,
            event.outcome.upper(),
            verdict.query_name,
            verdict.ref_name,
            verdict.reason,
        )
        if verdict.accepted:
            accepted_by_ref.setdefault(alignment.ref_name, []).append(verdict)

    results: dict[str, DepthResult] = {}
    for ref_name, ref_length in sorted(ref_map.items()):
        results[ref_name] = coverage_for_reference(
            ref_name,
            ref_length,
            accepted_by_ref.get(ref_name, []),
            dedup_policy=settings.dedup_policy,
        )

    if store is not None:
        _persist(
            store, run_id, request_id, settings, verdicts, results,
            raw_cigars,
        )

    return RunReport(
        run_id=run_id,
        request_id=request_id,
        results=results,
        verdicts=verdicts,
        diagnostics=events,
        input_count=len(verdicts),
        references=dict(sorted(ref_map.items())),
        settings=settings,
        external_sorted=use_external_sort,
    )


def analyze_lines(
    lines: Iterable[str],
    references: Iterable[Reference],
    settings: Settings | None = None,
    *,
    request_id: str | None = None,
    store: ProvenanceStore | None = None,
    use_external_sort: bool = True,
    tmp_dir: str | None = None,
) -> RunReport:
    """Analyze raw TSV lines; unparseable rows become undetermined verdicts."""
    from .external_sort import RecordCodecError, decode_record

    settings = settings or Settings()
    request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"

    decoded: list[Alignment] = []
    malformed: list[tuple[int, Verdict, dict]] = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            decoded.append(decode_record(line, lineno=index + 1))
        except (RecordCodecError, ValueError) as exc:
            verdict = _undetermined_verdict(
                index + 1, f"unparseable TSV row: {exc}"
            )
            malformed.append(
                (index + 1, verdict,
                 malformed_event(line, exc, request_id=request_id,
                                 record_index=index + 1).as_dict())
            )

    report = analyze(
        decoded,
        references,
        settings,
        request_id=request_id,
        store=store,
        use_external_sort=use_external_sort,
        tmp_dir=tmp_dir,
    )
    # Insert malformed verdicts in row order for stable accounting.
    extra_verdicts = [v for _, v, _ in sorted(malformed)]
    extra_events = [e for _, _, e in sorted(malformed)]
    if store is not None and extra_verdicts:
        store.append_undetermined(
            report.run_id, extra_verdicts, start_index=len(decoded)
        )
    report.verdicts.extend(extra_verdicts)
    report.diagnostics.extend(extra_events)
    report.input_count += len(extra_verdicts)
    return report


def _persist(
    store: ProvenanceStore,
    run_id: str,
    request_id: str,
    settings: Settings,
    verdicts: list[Verdict],
    results: dict[str, DepthResult],
    raw_cigars: dict[int, str],
) -> None:
    run = RunRecord(
        run_id=run_id,
        request_id=request_id,
        created_at=utc_now(),
        dedup_policy=settings.dedup_policy,
        min_mapq=settings.min_mapq,
        status="completed",
        input_count=len(verdicts),
        verdicts=verdicts,
        results=[results[k] for k in sorted(results)],
        raw_cigars=raw_cigars,
    )
    store.save_run(run)
