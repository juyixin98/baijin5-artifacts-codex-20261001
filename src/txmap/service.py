"""Application service: orchestrates repository, mapper and provenance.

Every mapping attempt -- accepted or rejected -- gets an audit record with a
request id and an audit id, plus the key state behind the decision. This is
also the layer where transcript identity isolation is enforced: a mapper is
built only from the rows belonging to the requested transcript id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .errors import MappingError, TranscriptNotFoundError
from .mapping import CoordinateMapper
from .models import IntervalMapping, PointMapping
from .storage import Repository


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fragment_dict(f: Any) -> dict[str, Any]:
    return {
        "tx_start": f.tx_start,
        "tx_end": f.tx_end,
        "genomic_start": f.genomic_start,
        "genomic_end": f.genomic_end,
        "length": f.length,
        "exon_index": f.exon_index,
        "genomic_order": f.genomic_order,
    }


def _point_dict(m: PointMapping) -> dict[str, Any]:
    return {
        "transcript_id": m.transcript_id,
        "strand": m.strand,
        "tx_position": m.tx_position,
        "genomic_position": m.genomic_position,
        "exon_index": m.exon_index,
    }


def _interval_dict(m: IntervalMapping) -> dict[str, Any]:
    return {
        "transcript_id": m.transcript_id,
        "strand": m.strand,
        "tx_start": m.tx_start,
        "tx_end": m.tx_end,
        "length": m.length,
        "mapped_length": m.mapped_length,
        "fragment_count": m.fragment_count,
        "fragments": [_fragment_dict(f) for f in m.fragments],
    }


class MappingService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    def _mapper(self, transcript_id: str) -> tuple[CoordinateMapper, str]:
        tx = self._repo.get_transcript(transcript_id)
        if tx is None:
            raise TranscriptNotFoundError(
                "unknown transcript id",
                transcript_id=transcript_id,
                known=self._repo.list_transcript_ids(),
            )
        pattern = self._repo.get_pattern(tx.chrom) or "ACGT"
        return CoordinateMapper(tx), pattern

    def transcript_info(self, transcript_id: str) -> dict[str, Any]:
        tx = self._repo.get_transcript(transcript_id)
        if tx is None:
            raise TranscriptNotFoundError(
                "unknown transcript id",
                transcript_id=transcript_id,
                known=self._repo.list_transcript_ids(),
            )
        return {
            "transcript_id": tx.transcript_id,
            "chrom": tx.chrom,
            "strand": tx.strand,
            "mature_length": tx.length,
            "genomic_span": list(tx.genomic_span()),
            "exons": [[e.start, e.end] for e in tx.exons],
        }

    def list_transcripts(self) -> list[dict[str, Any]]:
        return [self.transcript_info(tid) for tid in self._repo.list_transcript_ids()]

    def map_point(
        self, transcript_id: str, direction: str, position: int, request_id: str
    ) -> tuple[dict[str, Any], str]:
        audit_id = uuid.uuid4().hex
        payload = {"position": position}
        try:
            mapper, _pattern = self._mapper(transcript_id)
            result_obj = (
                mapper.tx_to_genomic_point(position)
                if direction == "tx_to_genomic"
                else mapper.genomic_to_tx_point(position)
            )
            result = _point_dict(result_obj)
            self._audit(audit_id, request_id, transcript_id, direction, "point",
                        payload, "mapped", result, None, None)
            return {"status": "mapped", "request_id": request_id,
                    "audit_id": audit_id, "result": result}, audit_id
        except MappingError as exc:
            self._audit(audit_id, request_id, transcript_id, direction, "point",
                        payload, "rejected", None, exc.code, exc.detail)
            raise

    def map_interval(
        self,
        transcript_id: str,
        direction: str,
        start: int,
        end: int,
        request_id: str,
    ) -> tuple[dict[str, Any], str]:
        audit_id = uuid.uuid4().hex
        payload = {"start": start, "end": end}
        try:
            mapper, _pattern = self._mapper(transcript_id)
            result_obj = (
                mapper.tx_to_genomic_interval(start, end)
                if direction == "tx_to_genomic"
                else mapper.genomic_to_tx_interval(start, end)
            )
            result = _interval_dict(result_obj)
            self._audit(audit_id, request_id, transcript_id, direction, "interval",
                        payload, "mapped", result, None, None)
            return {"status": "mapped", "request_id": request_id,
                    "audit_id": audit_id, "result": result}, audit_id
        except MappingError as exc:
            self._audit(audit_id, request_id, transcript_id, direction, "interval",
                        payload, "rejected", None, exc.code, exc.detail)
            raise

    def _audit(
        self,
        audit_id: str,
        request_id: str,
        transcript_id: str | None,
        direction: str,
        mode: str,
        input_payload: dict[str, Any],
        status: str,
        result: dict[str, Any] | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> None:
        self._repo.record_audit(
            {
                "audit_id": audit_id,
                "request_id": request_id,
                "transcript_id": transcript_id,
                "direction": direction,
                "mode": mode,
                "input": input_payload,
                "status": status,
                "result": result,
                "error_code": error_code,
                "error_detail": error_detail,
                "created_at": _utcnow(),
            }
        )

    def audit_trail(self, request_id: str) -> list[dict[str, Any]]:
        return self._repo.list_audit_by_request(request_id)
