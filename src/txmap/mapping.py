"""Domain mapping algorithm.

Coordinate convention is fixed (0-based, half-open, both systems). The mapper
is constructed per transcript and is immutable; identity isolation between
transcripts is achieved by never sharing state across mapper instances.

Plus strand ("+"):
    transcript position 0 is exon[0].start, ascending with the reference.

Minus strand ("-"):
    transcript position 0 is the LAST base of the LAST (highest) exon; the
    transcript traverses exons in descending genomic order. A transcript base
    is the reverse-complement of the reference base at the mapped position
    (sequence handling lives in ``sequence``; the coordinate mapping here only
    fixes position/orientation).

Intronic genomic coordinates are NEVER hard-snapped to the nearest exon:
point mapping raises IntronicPositionError and interval mapping raises
RegionNotMappableError.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import (
    CoordinateOutOfRangeError,
    IntronicPositionError,
    InvalidIntervalError,
    RegionNotMappableError,
)
from .models import Fragment, IntervalMapping, PointMapping, Transcript

# Per-item batch statuses (exceptions are used on the single-item API).
STATUS_MAPPED = "mapped"
STATUS_INVALID = "invalid"
STATUS_OUT_OF_RANGE = "coordinate_out_of_range"
STATUS_INTRONIC = "intronic_position"


@dataclass(frozen=True, slots=True)
class _Segment:
    """One exon expressed in transcript-ordered coordinates."""

    exon_index: int  # index in ascending-genomic exon list
    tx_start: int
    tx_end: int
    gen_start: int
    gen_end: int


class CoordinateMapper:
    """Maps coordinates for exactly one transcript."""

    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        exons = transcript.exons
        n = len(exons)

        starts = np.array([e.start for e in exons], dtype=np.int64)
        ends = np.array([e.end for e in exons], dtype=np.int64)
        lengths = ends - starts
        # prefix[i] = bases in genomic exons before i (ascending order)
        prefix = np.zeros(n + 1, dtype=np.int64)
        np.cumsum(lengths, out=prefix[1:])
        # suffix_base[i] = bases in genomic exons strictly AFTER i. On "-"
        # those are exactly the transcript bases preceding exon i.
        suffix_base = np.zeros(n, dtype=np.int64)
        suffix_base[:] = prefix[n] - prefix[1:]

        self._starts = starts
        self._ends = ends
        self._prefix = prefix
        self._suffix_base = suffix_base
        self.mature_length = int(prefix[n])

        if transcript.strand == "+":
            order = list(range(n))
            self._reverse_order = False
        else:
            order = list(range(n - 1, -1, -1))
            self._reverse_order = True

        # Transcript-ordered segments; running total follows the chosen order,
        # so tx_start is correct for BOTH strands (descending on "-").
        segments: list[_Segment] = []
        running = 0
        for genomic_index in order:
            length = int(lengths[genomic_index])
            segments.append(
                _Segment(
                    exon_index=genomic_index,
                    tx_start=running,
                    tx_end=running + length,
                    gen_start=int(starts[genomic_index]),
                    gen_end=int(ends[genomic_index]),
                )
            )
            running += length
        self._segments = segments
        self._seg_tx_end = np.array([s.tx_end for s in segments], dtype=np.int64)
        self._seg_tx_start = np.array([s.tx_start for s in segments], dtype=np.int64)
        self._seg_gen_start = np.array([s.gen_start for s in segments], dtype=np.int64)
        self._seg_gen_end = np.array([s.gen_end for s in segments], dtype=np.int64)

    # ------------------------------------------------------------------ points

    def tx_to_genomic_point(self, tx_position: int) -> PointMapping:
        p = self._require_int(tx_position, "tx_position")
        if p < 0:
            raise InvalidIntervalError("tx_position must be >= 0", tx_position=p)
        if p >= self.mature_length:
            raise CoordinateOutOfRangeError(
                "tx_position beyond mature transcript end",
                tx_position=p, mature_length=self.mature_length,
            )
        seg = self._segments[int(np.searchsorted(self._seg_tx_end, p, side="right"))]
        return PointMapping(
            transcript_id=self.transcript.transcript_id,
            strand=self.transcript.strand,
            tx_position=p,
            genomic_position=self._tx_offset_to_genomic(seg, p - seg.tx_start),
            exon_index=seg.exon_index,
        )

    def genomic_to_tx_point(self, genomic_position: int) -> PointMapping:
        g = self._require_int(genomic_position, "genomic_position")
        if g < 0:
            raise InvalidIntervalError("genomic_position must be >= 0", genomic_position=g)
        span_start, span_end = self.transcript.genomic_span()
        if g < span_start or g >= span_end:
            raise CoordinateOutOfRangeError(
                "genomic_position outside transcript span",
                genomic_position=g, span_start=span_start, span_end=span_end,
            )
        exon_index = self._find_genomic_exon(g)
        if exon_index is None:
            raise self._intronic_error(g, span_start, span_end)
        p = self._genomic_in_exon_to_tx(g, exon_index)
        return PointMapping(
            transcript_id=self.transcript.transcript_id,
            strand=self.transcript.strand,
            tx_position=p,
            genomic_position=g,
            exon_index=exon_index,
        )

    # ---------------------------------------------------------------- intervals

    def tx_to_genomic_interval(self, tx_start: int, tx_end: int) -> IntervalMapping:
        s, e = self._validate_half_open(tx_start, tx_end, self.mature_length, "tx")
        fragments: list[Fragment] = []
        for order, seg in enumerate(self._segments):
            if seg.tx_end <= s:
                continue
            if seg.tx_start >= e:
                break
            ts = max(s, seg.tx_start)
            te = min(e, seg.tx_end)
            o1, o2 = ts - seg.tx_start, te - seg.tx_start
            gs, ge = self._offset_span_to_genomic(seg, o1, o2)
            fragments.append(
                Fragment(
                    tx_start=ts, tx_end=te,
                    genomic_start=gs, genomic_end=ge,
                    exon_index=seg.exon_index,
                )
            )
        return IntervalMapping(
            transcript_id=self.transcript.transcript_id,
            strand=self.transcript.strand,
            tx_start=s, tx_end=e,
            fragments=self._annotate_genomic_order(fragments),
        )

    def genomic_to_tx_interval(
        self, genomic_start: int, genomic_end: int
    ) -> IntervalMapping:
        span_start, span_end = self.transcript.genomic_span()
        s, e = self._validate_half_open(
            genomic_start, genomic_end, span_end, "genomic", lower_bound=span_start
        )
        hits = self._exonic_overlaps(s, e)
        covered = sum(oe - os for os, oe, _ in hits)
        if covered != e - s:
            gaps = self._intronic_gaps(s, e, hits)
            raise RegionNotMappableError(
                "genomic interval overlaps intronic/intergenic sequence; "
                "it must not be hard-mapped",
                genomic_start=s, genomic_end=e, covered=covered,
                requested=e - s, intronic_gaps=gaps,
            )

        fragments: list[Fragment] = []
        for os_, oe_, exon_index in hits:
            ts, te = self._genomic_span_to_tx(exon_index, os_, oe_)
            fragments.append(
                Fragment(
                    tx_start=ts, tx_end=te,
                    genomic_start=os_, genomic_end=oe_,
                    exon_index=exon_index,
                )
            )
        # Return in transcript order (descending genomic on "-").
        if self._reverse_order:
            fragments.reverse()
        tx_start = fragments[0].tx_start
        tx_end = fragments[-1].tx_end
        return IntervalMapping(
            transcript_id=self.transcript.transcript_id,
            strand=self.transcript.strand,
            tx_start=tx_start, tx_end=tx_end,
            fragments=self._annotate_genomic_order(fragments),
        )

    # ------------------------------------------------------------ batch (numpy)

    def tx_points_to_genomic(
        self, positions: np.ndarray | list[int]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized point map. Returns (genomic_positions, statuses)."""
        pos = np.asarray(positions, dtype=np.int64)
        genomic = np.full(pos.shape, -1, dtype=np.int64)
        status = np.full(pos.shape, STATUS_MAPPED, dtype=object)
        invalid = pos < 0
        oor = pos >= self.mature_length
        status[invalid] = STATUS_INVALID
        status[oor & ~invalid] = STATUS_OUT_OF_RANGE
        mappable = ~(invalid | oor)
        idx = np.searchsorted(self._seg_tx_end, pos[mappable], side="right")
        offsets = pos[mappable] - self._seg_tx_start[idx]
        if self._reverse_order:
            genomic[mappable] = self._seg_gen_end[idx] - 1 - offsets
        else:
            genomic[mappable] = self._seg_gen_start[idx] + offsets
        return genomic, status

    def genomic_points_to_tx(
        self, positions: np.ndarray | list[int]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized point map. Returns (tx_positions, statuses)."""
        pos = np.asarray(positions, dtype=np.int64)
        tx = np.full(pos.shape, -1, dtype=np.int64)
        status = np.full(pos.shape, STATUS_MAPPED, dtype=object)
        span_start, span_end = self.transcript.genomic_span()
        invalid = pos < 0
        oor = (pos < span_start) | (pos >= span_end)
        status[invalid] = STATUS_INVALID
        status[oor & ~invalid] = STATUS_OUT_OF_RANGE
        candidates = ~(invalid | oor)
        idx = np.searchsorted(self._ends, pos[candidates], side="right")
        in_exon = idx < len(self._starts)
        cand_pos = pos[candidates]
        exon_hit = np.where(in_exon, idx, 0)
        inside = in_exon & (self._starts[exon_hit] <= cand_pos)

        final_idx = np.where(inside, exon_hit, 0)
        if self._reverse_order:
            mapped = self._suffix_base[final_idx] + (
                self._ends[final_idx] - 1 - cand_pos
            )
        else:
            mapped = self._prefix[final_idx] + (cand_pos - self._starts[final_idx])
        tx_candidates = np.where(inside, mapped, -1)
        tx[candidates] = tx_candidates
        status[candidates] = np.where(inside, STATUS_MAPPED, STATUS_INTRONIC)
        return tx, status

    # ------------------------------------------------------------------ helpers

    def _tx_offset_to_genomic(self, seg: _Segment, offset: int) -> int:
        if self._reverse_order:
            return seg.gen_end - 1 - offset
        return seg.gen_start + offset

    def _offset_span_to_genomic(self, seg: _Segment, o1: int, o2: int) -> tuple[int, int]:
        """Half-open transcript offsets [o1, o2) within one exon -> genomic."""
        if self._reverse_order:
            return seg.gen_end - o2, seg.gen_end - o1
        return seg.gen_start + o1, seg.gen_start + o2

    def _genomic_in_exon_to_tx(self, g: int, exon_index: int) -> int:
        if self._reverse_order:
            return int(
                self._suffix_base[exon_index]
                + (self._ends[exon_index] - 1 - g)
            )
        return int(self._prefix[exon_index] + (g - self._starts[exon_index]))

    def _genomic_span_to_tx(
        self, exon_index: int, gs: int, ge: int
    ) -> tuple[int, int]:
        """Half-open genomic sub-interval [gs, ge) of one exon -> tx coords."""
        if self._reverse_order:
            base = int(self._suffix_base[exon_index])
            return base + int(self._ends[exon_index] - ge), base + int(
                self._ends[exon_index] - gs
            )
        base = int(self._prefix[exon_index])
        return base + (gs - int(self._starts[exon_index])), base + (
            ge - int(self._starts[exon_index])
        )

    def _find_genomic_exon(self, g: int) -> int | None:
        idx = int(np.searchsorted(self._ends, g, side="right"))
        if idx < len(self._starts) and int(self._starts[idx]) <= g:
            return idx
        return None

    def _intronic_error(self, g: int, span_start: int, span_end: int) -> IntronicPositionError:
        idx = int(np.searchsorted(self._ends, g, side="right"))
        flanks = {}
        if idx > 0:
            flanks["prev_exon"] = [int(self._starts[idx - 1]), int(self._ends[idx - 1])]
        if idx < len(self._starts):
            flanks["next_exon"] = [int(self._starts[idx]), int(self._ends[idx])]
        return IntronicPositionError(
            "genomic_position is intronic and must not be hard-snapped to an exon",
            genomic_position=g, span_start=span_start, span_end=span_end, **flanks,
        )

    def _exonic_overlaps(
        self, s: int, e: int
    ) -> list[tuple[int, int, int]]:
        """Ascending-genomic (overlap_start, overlap_end, exon_index) hits."""
        hits: list[tuple[int, int, int]] = []
        for i in range(len(self._starts)):
            os_ = max(s, int(self._starts[i]))
            oe_ = min(e, int(self._ends[i]))
            if os_ < oe_:
                hits.append((os_, oe_, i))
        return hits

    @staticmethod
    def _intronic_gaps(
        s: int, e: int, hits: list[tuple[int, int, int]]
    ) -> list[list[int]]:
        gaps: list[list[int]] = []
        cursor = s
        for os_, oe_, _ in hits:
            if os_ > cursor:
                gaps.append([cursor, os_])
            cursor = oe_
        if cursor < e:
            gaps.append([cursor, e])
        return gaps

    @staticmethod
    def _annotate_genomic_order(fragments: list[Fragment]) -> tuple[Fragment, ...]:
        """Tag each fragment with its rank in ascending genomic order while
        keeping the list itself in transcript order."""
        ordered_indices = sorted(
            range(len(fragments)), key=lambda i: fragments[i].genomic_start
        )
        ranks = [0] * len(fragments)
        for rank, original_index in enumerate(ordered_indices):
            ranks[original_index] = rank
        return tuple(
            Fragment(
                tx_start=f.tx_start, tx_end=f.tx_end,
                genomic_start=f.genomic_start, genomic_end=f.genomic_end,
                exon_index=f.exon_index, genomic_order=ranks[i],
            )
            for i, f in enumerate(fragments)
        )

    @staticmethod
    def _validate_half_open(
        start: int,
        end: int,
        upper_bound: int,
        label: str,
        lower_bound: int = 0,
    ) -> tuple[int, int]:
        s = CoordinateMapper._require_int(start, f"{label}_start")
        e = CoordinateMapper._require_int(end, f"{label}_end")
        # Malformed shape first, regardless of domain bounds.
        if s > e:
            raise InvalidIntervalError(
                f"{label} interval must satisfy start <= end", start=s, end=e
            )
        if s == e:
            raise InvalidIntervalError(
                "zero-length intervals are not mappable; use a point request",
                start=s, end=e,
            )
        # Negative coordinates are malformed on either reference system;
        # a non-negative value below this domain's lower bound is out of range.
        if s < 0 or e < 0:
            raise InvalidIntervalError(
                f"{label} coordinates must be >= 0", start=s, end=e
            )
        if s < lower_bound:
            raise CoordinateOutOfRangeError(
                f"{label}_start below domain lower bound",
                start=s, lower_bound=lower_bound,
            )
        if e > upper_bound:
            raise CoordinateOutOfRangeError(
                f"{label}_end beyond domain upper bound (half-open)",
                end=e, upper_bound=upper_bound,
            )
        return s, e

    @staticmethod
    def _require_int(value: object, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidIntervalError(f"{name} must be an integer", value=repr(value))
        return value
