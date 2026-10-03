"""Core bidirectional coordinate mapping algorithms.

Conventions (see txmap.__init__): 0-based half-open everywhere; transcript
coordinates run 5'->3' along the spliced exonic sequence.

Key laws this module must satisfy (asserted by the test-suite):
- Round-trip: exonic g -> t -> g == g, and t -> g -> t == t.
- Length conservation: sum(fragment lengths) == number of mappable bases.
- Fragment order follows transcript order (5'->3'), never genomic order.
- Intronic positions are never hard-mapped; they surface as REJECTED/INTRONIC
  (points) or as explicit gaps (intervals).
"""

from __future__ import annotations

from typing import Dict, Tuple

from .intervals import ExonIndex
from .models import (
    Contig,
    Fragment,
    Gap,
    PointOutcome,
    IntervalOutcome,
    Reason,
    Status,
    Strand,
    Transcript,
)
from .reference import reverse_complement


class Mapper:
    """Maps coordinates for a fixed set of transcripts on fixed contigs."""

    def __init__(
        self,
        transcripts: Dict[str, Transcript],
        contigs: Dict[str, Contig],
    ) -> None:
        self._transcripts = dict(transcripts)
        self._contigs = dict(contigs)
        self._index = {tid: ExonIndex.build(tx) for tid, tx in transcripts.items()}

    # -- lookups -----------------------------------------------------------

    def transcript_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._transcripts))

    def get_transcript(self, tx_id: str) -> Transcript | None:
        return self._transcripts.get(tx_id)

    def _contig_length(self, tx: Transcript) -> int | None:
        contig = self._contigs.get(tx.contig)
        return contig.length if contig is not None else None

    # -- point mapping -----------------------------------------------------

    def genomic_to_transcript(self, tx_id: str, gpos: int) -> PointOutcome:
        tx = self._transcripts.get(tx_id)
        if tx is None:
            return PointOutcome(
                Status.REJECTED, Reason.UNKNOWN_TRANSCRIPT, tx_id, gpos, None
            )
        if gpos < 0:
            return PointOutcome(
                Status.REJECTED, Reason.INVALID_POSITION, tx_id, gpos, None
            )
        contig_len = self._contig_length(tx)
        if contig_len is None:
            return PointOutcome(
                Status.INDETERMINATE, Reason.UNKNOWN_CONTIG, tx_id, gpos, None
            )
        if gpos >= contig_len:
            return PointOutcome(
                Status.REJECTED, Reason.OUT_OF_CONTIG, tx_id, gpos, None
            )
        idx = self._index[tx_id]
        exon_i = idx.find_exon_genomic(gpos)
        if exon_i < 0:
            lo, hi = tx.genomic_span
            reason = (
                Reason.INTRONIC if lo <= gpos < hi else Reason.OUT_OF_TRANSCRIPT
            )
            return PointOutcome(Status.REJECTED, reason, tx_id, gpos, None)
        # Locate this exon in transcript order and compute the offset.
        k = int((idx.tx_exon_genomic_idx == exon_i).nonzero()[0][0])
        if tx.strand is Strand.PLUS:
            offset = gpos - int(idx.g_starts[exon_i])
        else:
            offset = int(idx.g_ends[exon_i]) - 1 - gpos
        tpos = int(idx.tx_cum[k]) + offset
        return PointOutcome(Status.OK, Reason.OK, tx_id, gpos, tpos)

    def transcript_to_genomic(self, tx_id: str, tpos: int) -> PointOutcome:
        tx = self._transcripts.get(tx_id)
        if tx is None:
            return PointOutcome(
                Status.REJECTED, Reason.UNKNOWN_TRANSCRIPT, tx_id, tpos, None
            )
        if tpos < 0:
            return PointOutcome(
                Status.REJECTED, Reason.INVALID_POSITION, tx_id, tpos, None
            )
        idx = self._index[tx_id]
        k = idx.find_exon_tx(tpos)
        if k < 0:
            return PointOutcome(
                Status.REJECTED, Reason.OUT_OF_TRANSCRIPT, tx_id, tpos, None
            )
        exon_i = int(idx.tx_exon_genomic_idx[k])
        offset = tpos - int(idx.tx_cum[k])
        if tx.strand is Strand.PLUS:
            gpos = int(idx.g_starts[exon_i]) + offset
        else:
            gpos = int(idx.g_ends[exon_i]) - 1 - offset
        return PointOutcome(Status.OK, Reason.OK, tx_id, tpos, gpos)

    # -- interval mapping --------------------------------------------------

    def genomic_interval_to_transcript(
        self, tx_id: str, g_start: int, g_end: int
    ) -> IntervalOutcome:
        """Map a genomic interval; intronic sub-ranges become explicit gaps.

        Fragments are returned in transcript order (5'->3'), each carrying
        both genomic and transcript half-open coordinates.
        """
        rejected = self._reject_interval_prereqs(tx_id, g_start, g_end)
        if rejected is not None:
            return rejected
        tx = self._transcripts[tx_id]
        idx = self._index[tx_id]
        contig_len = self._contig_length(tx)
        assert contig_len is not None  # guaranteed by prereqs
        if g_end > contig_len:
            return IntervalOutcome(
                Status.REJECTED, Reason.OUT_OF_CONTIG, tx_id, (), ()
            )

        fragments: list[Fragment] = []
        gaps: list[Gap] = []
        cursor = g_start
        for exon in tx.exons:
            ov_start = max(exon.start, g_start)
            ov_end = min(exon.end, g_end)
            if ov_start >= ov_end:
                continue
            if cursor < ov_start:
                gaps.append(Gap(cursor, ov_start, self._gap_reason(tx, cursor)))
            fragments.append(self._genomic_block_to_tx(idx, exon_i_of(tx, exon), ov_start, ov_end))
            cursor = ov_end
        if cursor < g_end:
            gaps.append(Gap(cursor, g_end, self._gap_reason(tx, cursor)))

        if not fragments:
            return IntervalOutcome(
                Status.REJECTED, gaps[0].reason, tx_id, (), tuple(gaps)
            )
        # Fragments were collected in genomic ascending order; transcript
        # order is the same for + and reversed for -.
        if tx.strand is Strand.MINUS:
            fragments.reverse()
        status = Status.OK if not gaps else Status.PARTIAL
        return IntervalOutcome(status, Reason.OK, tx_id, tuple(fragments), tuple(gaps))

    def transcript_interval_to_genomic(
        self, tx_id: str, t_start: int, t_end: int
    ) -> IntervalOutcome:
        """Map a transcript interval; always fully exonic, so no gaps."""
        rejected = self._reject_interval_prereqs(tx_id, t_start, t_end)
        if rejected is not None:
            return rejected
        tx = self._transcripts[tx_id]
        idx = self._index[tx_id]
        if t_end > idx.tx_length:
            return IntervalOutcome(
                Status.REJECTED, Reason.OUT_OF_TRANSCRIPT, tx_id, (), ()
            )

        fragments: list[Fragment] = []
        for k in range(len(idx.tx_cum)):
            ex_lo = int(idx.tx_cum[k])
            ex_hi = ex_lo + self._exon_len(idx, k)
            ov_start = max(ex_lo, t_start)
            ov_end = min(ex_hi, t_end)
            if ov_start >= ov_end:
                continue
            exon_i = int(idx.tx_exon_genomic_idx[k])
            o1 = ov_start - ex_lo
            o2 = ov_end - ex_lo
            if tx.strand is Strand.PLUS:
                gs = int(idx.g_starts[exon_i]) + o1
                ge = int(idx.g_starts[exon_i]) + o2
            else:
                gs = int(idx.g_ends[exon_i]) - o2
                ge = int(idx.g_ends[exon_i]) - o1
            fragments.append(Fragment(gs, ge, ov_start, ov_end))
        return IntervalOutcome(Status.OK, Reason.OK, tx_id, tuple(fragments), ())

    # -- sequence / base direction -----------------------------------------

    def spliced_sequence(self, tx_id: str) -> Tuple[Status, Reason, str | None]:
        """Spliced exonic sequence in transcript orientation (5'->3').

        Minus-strand transcripts get the reverse complement of the genomic
        exon concatenation, so base direction follows the transcript.
        """
        tx = self._transcripts.get(tx_id)
        if tx is None:
            return Status.REJECTED, Reason.UNKNOWN_TRANSCRIPT, None
        contig = self._contigs.get(tx.contig)
        if contig is None:
            return Status.INDETERMINATE, Reason.SEQUENCE_UNAVAILABLE, None
        # Exons are stored genomic-ascending. For minus strand, the spliced
        # transcript (5'->3') is the reverse complement of the ascending
        # concatenation: revcomp(X+Y+Z) = revcomp(Z)+revcomp(Y)+revcomp(X),
        # which is exactly the transcript-ordered exon sequences.
        pieces = [contig.sequence[e.start : e.end] for e in tx.exons]
        if tx.strand is Strand.MINUS:
            return Status.OK, Reason.OK, reverse_complement("".join(pieces))
        return Status.OK, Reason.OK, "".join(pieces)

    # -- internals ---------------------------------------------------------

    def _reject_interval_prereqs(
        self, tx_id: str, start: int, end: int
    ) -> IntervalOutcome | None:
        tx = self._transcripts.get(tx_id)
        if tx is None:
            return IntervalOutcome(
                Status.REJECTED, Reason.UNKNOWN_TRANSCRIPT, tx_id, (), ()
            )
        if start < 0 or end < 0:
            return IntervalOutcome(
                Status.REJECTED, Reason.INVALID_POSITION, tx_id, (), ()
            )
        if end <= start:
            return IntervalOutcome(
                Status.REJECTED, Reason.INVALID_INTERVAL, tx_id, (), ()
            )
        if self._contig_length(tx) is None:
            return IntervalOutcome(
                Status.INDETERMINATE, Reason.UNKNOWN_CONTIG, tx_id, (), ()
            )
        return None

    @staticmethod
    def _exon_len(idx: ExonIndex, k: int) -> int:
        exon_i = int(idx.tx_exon_genomic_idx[k])
        return int(idx.g_ends[exon_i] - idx.g_starts[exon_i])

    @staticmethod
    def _gap_reason(tx: Transcript, gpos: int) -> Reason:
        lo, hi = tx.genomic_span
        return Reason.INTRONIC if lo <= gpos < hi else Reason.OUT_OF_TRANSCRIPT

    def _genomic_block_to_tx(
        self, idx: ExonIndex, exon_i: int, g_start: int, g_end: int
    ) -> Fragment:
        """Convert one exonic genomic block to a Fragment with tx coords."""
        k = int((idx.tx_exon_genomic_idx == exon_i).nonzero()[0][0])
        cum = int(idx.tx_cum[k])
        if idx.strand is Strand.PLUS:
            t_start = cum + (g_start - int(idx.g_starts[exon_i]))
            t_end = cum + (g_end - int(idx.g_starts[exon_i]))
        else:
            t_start = cum + (int(idx.g_ends[exon_i]) - g_end)
            t_end = cum + (int(idx.g_ends[exon_i]) - g_start)
        return Fragment(g_start, g_end, t_start, t_end)


def exon_i_of(tx: Transcript, exon) -> int:
    """Genomic-ascending index of an exon within its transcript."""
    for i, e in enumerate(tx.exons):
        if e is exon:
            return i
    raise ValueError("exon not part of transcript")  # pragma: no cover
