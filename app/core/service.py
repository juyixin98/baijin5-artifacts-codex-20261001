"""Stream state machine: the policy layer.

Everything security-relevant about *accepting* segments lives here:

* verify-before-stage: a segment is AEAD-opened and its plaintext length
  checked before any plaintext touches disk;
* nonce discipline: a slot already holding different ciphertext (or a
  different terminator flag) is a NONCE_CONFLICT; an identical retry is an
  idempotent replay;
* completion is decided only from authenticated state: every seqno present,
  exactly one terminator on the last slot, declared length assembled;
* the complete plaintext is released once; any failure shreds fragments and
  seals the stream as failed;
* every decision (accept/reject/inconclusive) gets a correlated audit record
  carrying request id and non-sensitive state.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any

from .audit import AuditLog
from .crypto import AEADBackend
from .errors import (
    AuthenticationError, IncompleteStreamError, LimitError,
    NonceConflictError, ProtocolCodingError, StateError, StorageError,
    TruncationError,
)
from .keyring import KeyBundle
from .protocol import (
    PROTOCOL_VERSION, TAG_LEN, decode_frame, derive_nonce, encode_aad,
)
from .staging import StagingArea
from ..db.store import (
    ACTIVE_STATUSES, FAILED, INTERRUPTED, RECEIVING, RELEASED, Store,
)


def ct_digest(ciphertext: bytes) -> str:
    return hashlib.sha256(ciphertext).hexdigest()


@dataclass(frozen=True)
class Acceptance:
    accepted: bool
    complete: bool
    message_id: str
    seqno: int
    received: int
    total_segments: int
    replay: bool = False
    plaintext: bytes | None = None  # populated exactly once, on release


class SegmentedAEADService:
    def __init__(self, store: Store, staging: StagingArea,
                 bundle: KeyBundle, backend: AEADBackend,
                 audit: AuditLog | None = None,
                 max_segment_bytes: int = 1 << 20,
                 max_total_bytes: int = 64 << 20) -> None:
        self._store = store
        self._staging = staging
        self._bundle = bundle
        self._backend = backend
        self._audit = audit or AuditLog()
        self._max_seg = max_segment_bytes
        self._max_total = max_total_bytes
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ---- public API --------------------------------------------------------

    def recover_interrupted(self) -> int:
        """Mark previously receiving streams interrupted and reconcile files.

        Called once at process start.  Segment rows whose staged fragment
        vanished with the temp filesystem are dropped so the stream is
        resumable by retransmission rather than stuck.
        """
        count = self._store.mark_interrupted_on_startup()
        import os
        for stream in self._store.list_streams():
            if stream["status"] != INTERRUPTED:
                continue
            for seg in self._store.list_segments(stream["message_id"]):
                if not os.path.exists(seg["staged_path"]):
                    self._store.drop_segment(stream["message_id"],
                                             seg["seqno"])
        if count:
            self._audit.event("lifecycle", "interrupted", None,
                              "streams marked interrupted on startup",
                              streams=count)
        return count

    def begin_stream(self, message_id: str, total_segments: int,
                     total_len: int, request_id: str | None = None) -> None:
        """Explicit stream registration (optional; first frame also opens)."""
        self._validate_decl(total_segments, total_len)
        existing = self._store.get_stream(message_id)
        if existing is not None:
            raise StateError("stream already registered",
                             request_id=request_id,
                             status=existing["status"])
        self._store.begin_stream(message_id, self._bundle.key_id,
                                 total_segments, total_len)
        self._audit.event("lifecycle", "stream_opened", request_id,
                          "stream registered", message_id=message_id,
                          total_segments=total_segments, total_len=total_len)

    def submit(self, frame_bytes: bytes,
               request_id: str | None = None) -> Acceptance:
        """Verify, stage and account for one segment frame."""
        frame = decode_frame(frame_bytes)  # raises PROTOCODING
        message_id = frame.message_id.decode("utf-8")
        lock = self._lock_for(message_id)
        with lock:
            return self._submit_locked(frame, message_id, request_id)

    def finalize(self, message_id: str,
                 request_id: str | None = None) -> Acceptance:
        """Attempt release; fails with INCOMPLETE or TRUNCATION if undecidable."""
        with self._lock_for(message_id):
            return self._finalize_locked(message_id, request_id)

    def get_result(self, message_id: str) -> bytes:
        """Return released plaintext; never expose partial plaintext."""
        stream = self._require_stream(message_id)
        if stream["status"] != RELEASED:
            raise StateError("stream not released", status=stream["status"])
        data = self._staging.read_released(message_id)
        if data is None:
            raise StorageError("released artifact missing")
        return data

    def status(self, message_id: str) -> dict[str, Any]:
        stream = self._require_stream(message_id)
        segs = self._store.list_segments(message_id)
        return {
            "message_id": message_id,
            "status": stream["status"],
            "total_segments": stream["total_segments"],
            "total_len": stream["total_len"],
            "received_count": len(segs),
            "received_seqnos": sorted(s["seqno"] for s in segs),
            "have_terminator": any(s["is_final"] for s in segs),
            "released": stream["status"] == RELEASED,
        }

    def abort(self, message_id: str, request_id: str | None = None) -> None:
        with self._lock_for(message_id):
            stream = self._require_stream(message_id)
            self._check_active(stream, request_id)
            self._fail(message_id, "aborted", request_id,
                       category="abort", audit_kind="lifecycle")

    def audit_events(self, message_id: str | None = None,
                     limit: int = 200) -> list[dict[str, Any]]:
        return self._store.list_audit(message_id, limit)

    def released_path(self, message_id: str) -> str:
        stream = self._require_stream(message_id)
        if stream["status"] != RELEASED:
            raise StateError("stream not released", status=stream["status"])
        path = self._staging.released_path(message_id)
        if path is None:
            raise StorageError("released artifact missing")
        return str(path)

    # ---- internals ---------------------------------------------------------

    def _lock_for(self, message_id: str) -> threading.Lock:
        with self._locks_guard:
            lk = self._locks.get(message_id)
            if lk is None:
                lk = threading.Lock()
                self._locks[message_id] = lk
            return lk

    def _validate_decl(self, total_segments: int, total_len: int) -> None:
        if total_segments < 1:
            raise ProtocolCodingError("total_segments must be >= 1",
                                      total_segments=total_segments)
        if total_len < 0:
            raise ProtocolCodingError("total_len must be >= 0",
                                      total_len=total_len)
        if total_len > self._max_total:
            raise LimitError("total_len exceeds limit",
                             total_len=total_len, limit=self._max_total)

    def _submit_locked(self, frame, message_id: str,
                       request_id: str | None) -> Acceptance:
        if frame.version != PROTOCOL_VERSION:
            raise ProtocolCodingError("unsupported version",
                                      version=frame.version)
        self._validate_decl(frame.total_segments, frame.total_len)
        if frame.plaintext_len > self._max_seg:
            raise LimitError("segment larger than limit",
                             seqno=frame.seqno,
                             segment_len=frame.plaintext_len,
                             limit=self._max_seg)

        stream = self._store.get_stream(message_id)
        if stream is None:
            stream = self._store.begin_stream(
                message_id, self._bundle.key_id,
                frame.total_segments, frame.total_len)
            self._audit.event("lifecycle", "stream_opened", request_id,
                              "stream opened by first segment",
                              message_id=message_id,
                              total_segments=frame.total_segments,
                              total_len=frame.total_len)
        else:
            self._check_active(stream, request_id)
            if stream["total_segments"] != frame.total_segments:
                raise ProtocolCodingError(
                    "total_segments disagrees with stream",
                    declared=frame.total_segments,
                    stream_value=stream["total_segments"])
            if stream["total_len"] != frame.total_len:
                raise ProtocolCodingError(
                    "total_len disagrees with stream",
                    declared=frame.total_len,
                    stream_value=stream["total_len"])
            if stream["status"] == INTERRUPTED:
                self._store.resume_stream(message_id)

        # Terminator must sit on the final slot.
        if frame.is_final != (frame.seqno == frame.total_segments - 1):
            self._fail(message_id, "terminator on wrong slot", request_id,
                       seqno=frame.seqno, total_segments=frame.total_segments)
            raise TruncationError("terminator flag on wrong segment",
                                  request_id=request_id,
                                  seqno=frame.seqno)

        # Nonce discipline: same slot, same terminator marker => same nonce.
        existing = self._store.get_segment(message_id, frame.seqno)
        digest = ct_digest(frame.ciphertext)
        if existing is not None:
            if (existing["ct_hash"] == digest
                    and bool(existing["is_final"]) == frame.is_final):
                self._record_audit(message_id, "accept", "replay", request_id,
                                   "identical retry accepted idempotently",
                                   seqno=frame.seqno)
                return self._acceptance(message_id, frame.seqno, replay=True)
            self._fail(message_id, "nonce reused with different content",
                       request_id, seqno=frame.seqno)
            raise NonceConflictError(
                "segment slot reused with different ciphertext "
                "(nonce reuse attempt)",
                request_id=request_id, message_id=message_id,
                seqno=frame.seqno)

        # Verify BEFORE staging.
        nonce = derive_nonce(self._bundle.nonce_key, frame.message_id,
                             frame.seqno, frame.is_final)
        aad = encode_aad(frame.message_id, frame.seqno, frame.total_segments,
                         frame.is_final, frame.total_len, frame.plaintext_len)
        try:
            plaintext = self._backend.open(
                self._bundle.aead_key, nonce, frame.ciphertext, aad)
        except AuthenticationError:
            self._fail(message_id, "AEAD tag verification failed",
                       request_id, seqno=frame.seqno)
            raise
        if len(plaintext) != frame.plaintext_len:
            self._fail(message_id, "segment length mismatch", request_id,
                       seqno=frame.seqno)
            raise ProtocolCodingError("authenticated length mismatch",
                                      seqno=frame.seqno)

        path = self._staging.stage(message_id, frame.seqno, plaintext)
        self._store.add_segment(
            message_id, frame.seqno, frame.is_final, nonce, digest,
            len(frame.ciphertext), frame.plaintext_len, path)
        self._store.bump_stream(message_id, frame.seqno, len(plaintext))
        self._record_audit(message_id, "accept", "segment_accepted",
                           request_id, "segment authenticated and staged",
                           seqno=frame.seqno,
                           segment_len=frame.plaintext_len)

        return self._acceptance(message_id, frame.seqno)

    def _acceptance(self, message_id: str, seqno: int,
                    replay: bool = False) -> Acceptance:
        stream = self._store.get_stream(message_id)
        segs = self._store.list_segments(message_id)
        complete = self._is_complete(stream, segs)
        return Acceptance(
            accepted=True, complete=complete, message_id=message_id,
            seqno=seqno, received=len(segs),
            total_segments=stream["total_segments"], replay=replay)

    @staticmethod
    def _is_complete(stream: dict[str, Any],
                     segs: list[dict[str, Any]]) -> bool:
        if stream["status"] != RECEIVING:
            return False
        n = stream["total_segments"]
        if len(segs) != n:
            return False
        seqnos = {s["seqno"] for s in segs}
        if seqnos != set(range(n)):
            return False
        finals = [s for s in segs if s["is_final"]]
        return len(finals) == 1 and finals[0]["seqno"] == n - 1

    def _finalize_locked(self, message_id: str,
                         request_id: str | None) -> Acceptance:
        stream = self._require_stream(message_id)
        self._check_active(stream, request_id)
        segs = self._store.list_segments(message_id)

        # Crash recovery: startup reconciled receipts against staged files
        # (dropping any receipt whose fragment vanished).  If every receipt
        # survived and the set is structurally complete, an interrupted
        # stream can resume without a full retransmission.
        if (stream["status"] == INTERRUPTED
                and self._is_complete(
                    {**stream, "status": RECEIVING}, segs)):
            self._store.resume_stream(message_id)
            stream = self._require_stream(message_id)
            self._record_audit(
                message_id, "lifecycle", "resumed", request_id,
                "interrupted stream resumed for release after reconciliation",
                segments=len(segs))

        if len(segs) < stream["total_segments"]:
            self._inconclusive(message_id, "segments outstanding", request_id,
                               received=len(segs),
                               total=stream["total_segments"])
            raise IncompleteStreamError(
                "cannot decide: segments still outstanding",
                request_id=request_id, received=len(segs),
                total=stream["total_segments"])

        if not self._is_complete(stream, segs):
            self._fail(message_id, "all slots filled but terminator invalid",
                       request_id, received=len(segs))
            raise TruncationError(
                "stream truncated: missing or misplaced terminator",
                request_id=request_id)

        return self._release(message_id, stream, request_id)

    def _release(self, message_id: str, stream: dict[str, Any],
                 request_id: str | None) -> Acceptance:
        try:
            plaintext = self._staging.release(
                message_id, stream["total_segments"], stream["total_len"])
        except StorageError:
            self._fail(message_id, "release assembly failed", request_id)
            raise
        self._store.mark_released(message_id)
        self._record_audit(message_id, "accept", "stream_released",
                           request_id,
                           "full stream authenticated and released",
                           segments=stream["total_segments"],
                           total_len=stream["total_len"])
        return Acceptance(
            accepted=True, complete=True, message_id=message_id, seqno=-1,
            received=stream["total_segments"],
            total_segments=stream["total_segments"], plaintext=plaintext)

    def _record_audit(self, message_id: str, kind: str, category: str,
                      request_id: str | None, reason: str,
                      **state: object) -> None:
        """Persist an audit row AND emit the log line (both redaction-safe)."""
        self._store.audit(message_id, kind, category, request_id, reason,
                          dict(state))
        self._audit.event(kind, category, request_id, reason,
                          message_id=message_id, **state)

    def _fail(self, message_id: str, reason: str, request_id: str | None,
              category: str = "rejected", audit_kind: str = "reject",
              **state: object) -> None:
        self._staging.shred(message_id)
        self._store.delete_segments(message_id)
        self._store.mark_status(message_id, FAILED)
        self._record_audit(message_id, audit_kind, category, request_id,
                           reason, **state)

    def _inconclusive(self, message_id: str, reason: str,
                      request_id: str | None, **state: object) -> None:
        self._record_audit(message_id, "inconclusive", "incomplete",
                           request_id, reason, **state)

    def _require_stream(self, message_id: str) -> dict[str, Any]:
        stream = self._store.get_stream(message_id)
        if stream is None:
            raise StateError("unknown stream")
        return stream

    def _check_active(self, stream: dict[str, Any],
                      request_id: str | None) -> None:
        if stream["status"] not in ACTIVE_STATUSES:
            raise StateError("stream sealed", request_id=request_id,
                             status=stream["status"])
