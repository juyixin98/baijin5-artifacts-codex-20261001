"""Service layer: chunked authenticated encryption with staged release.

Flow
----
1. ``create_message``   - allocate message id, salt and nonce base.
2. ``submit_chunk``     - encrypt one chunk (any order).  The nonce is
   derived from (nonce_base, seq); the store enforces one record per
   (message, seq), so a nonce can never be issued twice.  A retry with
   identical content is answered from the stored record (no
   re-encryption); a retry with *different* content under the same seq
   is rejected as a nonce-reuse conflict.
3. ``finalize``         - verify stream completeness (contiguous seqs,
   exactly one final flag on the last chunk), authenticate every chunk
   with the core AEAD *and* the independent verifier, then publish the
   assembled plaintext in a single transaction.  Until this succeeds,
   no plaintext is available anywhere.
"""

from __future__ import annotations

import os
import sqlite3
import time

from . import protocol
from .audit import ACCEPT, REJECT, AuditLog, content_hash
from .config import Settings
from .crypto_aead import CoreAEAD, TagVerificationError
from .crypto_verify import IndependentVerifier
from .errors import (
    FinalFlagMisplaced,
    IncompleteStream,
    MessageStateConflict,
    NonceReuseConflict,
    NotFound,
    TagMismatch,
)
from .store import Store


class ChunkedCryptoService:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        core: CoreAEAD | None = None,
        verifier: IndependentVerifier | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or Store(settings.db_path)
        self._core = core or CoreAEAD()
        self._verifier = verifier or IndependentVerifier()
        self._audit = AuditLog(self._store)

    # -- helpers ---------------------------------------------------------

    def close(self) -> None:
        self._store.close()

    @property
    def audit(self) -> AuditLog:
        return self._audit

    def _require_message(self, message_id: str) -> dict:
        msg = self._store.get_message(message_id)
        if msg is None:
            raise NotFound("unknown message id", message_id=message_id)
        return msg

    def _require_open(self, message_id: str) -> dict:
        msg = self._require_message(message_id)
        if msg["status"] != "open":
            raise MessageStateConflict(
                f"message is {msg['status']}, not open",
                message_id=message_id,
                status=msg["status"],
            )
        return msg

    def _message_key(self, msg: dict) -> bytes:
        return protocol.derive_message_key(
            self._settings.master_key,
            bytes(msg["salt"]),
            bytes.fromhex(msg["message_id"]),
        )

    # -- message lifecycle -------------------------------------------------

    def create_message(
        self,
        *,
        request_id: str,
        message_id: str | None = None,
        salt: bytes | None = None,
        nonce_base: bytes | None = None,
    ) -> dict:
        """Allocate a new message.

        ``message_id``/``salt``/``nonce_base`` may be supplied explicitly
        to provision reproducible streams (fixtures, migrations); by
        default they are random.
        """
        msg_id_bytes = (
            bytes.fromhex(message_id) if message_id else os.urandom(protocol.MSG_ID_LEN)
        )
        if len(msg_id_bytes) != protocol.MSG_ID_LEN:
            raise ValueError("message_id must be 16 bytes (32 hex chars)")
        salt = salt or os.urandom(protocol.SALT_LEN)
        nonce_base = nonce_base or os.urandom(protocol.NONCE_BASE_LEN)
        if len(salt) != protocol.SALT_LEN or len(nonce_base) != protocol.NONCE_BASE_LEN:
            raise ValueError("bad salt/nonce_base length")
        mid = msg_id_bytes.hex()
        if self._store.get_message(mid) is not None:
            raise MessageStateConflict("message id already exists", message_id=mid)
        self._store.create_message(
            message_id=mid, salt=salt, nonce_base=nonce_base, created_at=time.time()
        )
        self._audit.record(
            request_id=request_id,
            message_id=mid,
            event="message_created",
            decision=ACCEPT,
            reason="message stream allocated",
        )
        return {"message_id": mid}

    def submit_chunk(
        self,
        *,
        request_id: str,
        message_id: str,
        seq: int,
        final: bool,
        plaintext: bytes,
    ) -> dict:
        """Encrypt and stage one chunk.  Idempotent per (message, seq)."""
        msg = self._require_open(message_id)
        if not 0 <= seq < self._settings.max_chunks:
            raise ValueError("seq out of range")
        if len(plaintext) > self._settings.max_chunk_bytes:
            raise ValueError("chunk too large")

        pt_hash = content_hash(plaintext)
        aad = protocol.encode_aad(bytes.fromhex(message_id), seq, final, len(plaintext))
        nonce = protocol.derive_nonce(bytes(msg["nonce_base"]), seq)
        key = self._message_key(msg)
        ct = self._core.encrypt(key, nonce, aad, plaintext)

        try:
            self._store.insert_chunk(
                message_id=message_id,
                seq=seq,
                request_id=request_id,
                final=final,
                pt_sha256=pt_hash,
                ct=ct,
                aad=aad,
                created_at=time.time(),
            )
        except sqlite3.IntegrityError:
            existing = self._store.get_chunk(message_id, seq)
            assert existing is not None
            if existing["pt_sha256"] == pt_hash and bool(existing["final"]) == final:
                # Same content retried: answer from the stored record,
                # never re-encrypt under the same nonce.
                self._audit.record(
                    request_id=request_id,
                    message_id=message_id,
                    event="chunk_replayed",
                    decision=ACCEPT,
                    reason="identical retry served from stored record",
                    detail={"seq": seq, "pt_sha256": pt_hash},
                )
                return {
                    "message_id": message_id,
                    "seq": seq,
                    "ciphertext": bytes(existing["ct"]),
                    "replayed": True,
                }
            self._audit.record(
                request_id=request_id,
                message_id=message_id,
                event="chunk_rejected",
                decision=REJECT,
                reason="different content under an already-used (message, seq) nonce",
                detail={
                    "seq": seq,
                    "stored_pt_sha256": existing["pt_sha256"],
                    "new_pt_sha256": pt_hash,
                },
            )
            raise NonceReuseConflict(
                "seq already used with different content; refusing to"
                " re-encrypt under the same nonce",
                message_id=message_id,
                seq=seq,
            )

        self._audit.record(
            request_id=request_id,
            message_id=message_id,
            event="chunk_accepted",
            decision=ACCEPT,
            reason="chunk encrypted and staged",
            detail={"seq": seq, "final": final, "pt_len": len(plaintext),
                    "pt_sha256": pt_hash},
        )
        return {
            "message_id": message_id,
            "seq": seq,
            "ciphertext": ct,
            "replayed": False,
        }

    # -- finalization ------------------------------------------------------

    def _check_completeness(self, message_id: str, chunks: list[dict]) -> None:
        if not chunks:
            raise IncompleteStream("no chunks received", message_id=message_id)
        seqs = [c["seq"] for c in chunks]
        n = len(seqs)
        expected = list(range(n))
        if seqs != expected:
            missing = sorted(set(expected) - set(seqs))
            raise IncompleteStream(
                "sequence numbers are not contiguous",
                message_id=message_id,
                missing=missing,
                received=n,
            )
        final_seqs = [c["seq"] for c in chunks if c["final"]]
        if not final_seqs:
            raise IncompleteStream(
                "terminating chunk (final flag) missing",
                message_id=message_id,
                received=n,
            )
        if final_seqs != [n - 1]:
            raise FinalFlagMisplaced(
                "final flag must appear exactly once, on the last chunk",
                message_id=message_id,
                final_seqs=final_seqs,
                last_seq=n - 1,
            )

    def _authenticate_chunks(self, msg: dict, chunks: list[dict]) -> bytes:
        """Decrypt and cross-verify every chunk; return assembled plaintext."""
        message_id = msg["message_id"]
        key = self._message_key(msg)
        msg_id_bytes = bytes.fromhex(message_id)
        parts: list[bytes] = []
        for chunk in chunks:
            seq = chunk["seq"]
            final = bool(chunk["final"])
            ct = bytes(chunk["ct"])
            # Recompute AAD from first principles and require it to match
            # the stored value (defense against staged-record tampering).
            aad = protocol.encode_aad(msg_id_bytes, seq, final,
                                      len(ct) - protocol.TAG_LEN)
            if aad != bytes(chunk["aad"]):
                raise TagMismatch(
                    "stored AAD does not match recomputed AAD",
                    message_id=message_id,
                    seq=seq,
                )
            nonce = protocol.derive_nonce(bytes(msg["nonce_base"]), seq)
            try:
                pt = self._core.decrypt(key, nonce, aad, ct)
            except TagVerificationError as exc:
                raise TagMismatch(
                    "core AEAD rejected chunk",
                    message_id=message_id,
                    seq=seq,
                ) from exc
            # Second opinion from the independent implementation.
            if not self._verifier.verify(key, nonce, aad, ct):
                raise TagMismatch(
                    "independent verifier rejected chunk",
                    message_id=message_id,
                    seq=seq,
                )
            if content_hash(pt) != chunk["pt_sha256"]:
                raise TagMismatch(
                    "decrypted content hash differs from staged hash",
                    message_id=message_id,
                    seq=seq,
                )
            parts.append(pt)
        return b"".join(parts)

    def _before_release_commit(self, message_id: str) -> None:
        """Hook executed just before the release transaction.

        Exists so tests can inject a crash at the worst possible moment
        and prove that no plaintext escapes and finalization is
        restartable.  No-op in production.
        """

    def finalize(self, *, request_id: str, message_id: str) -> dict:
        msg = self._require_open(message_id)
        chunks = self._store.list_chunks(message_id)
        try:
            self._check_completeness(message_id, chunks)
            plaintext = self._authenticate_chunks(msg, chunks)
        except (IncompleteStream, FinalFlagMisplaced, TagMismatch) as exc:
            if isinstance(exc, TagMismatch):
                self._store.set_status(message_id, "failed",
                                       fail_reason=exc.category)
            self._audit.record(
                request_id=request_id,
                message_id=message_id,
                event="finalize_rejected",
                decision=REJECT,
                reason=exc.reason,
                detail={"category": exc.category, **exc.context},
            )
            raise

        self._before_release_commit(message_id)
        pt_hash = content_hash(plaintext)
        self._store.release(
            message_id=message_id,
            plaintext=plaintext,
            plaintext_sha256=pt_hash,
            chunk_count=len(chunks),
            released_at=time.time(),
        )
        self._audit.record(
            request_id=request_id,
            message_id=message_id,
            event="finalize_accepted",
            decision=ACCEPT,
            reason="stream complete and authenticated; plaintext released",
            detail={"chunks": len(chunks), "size": len(plaintext),
                    "plaintext_sha256": pt_hash},
        )
        return {
            "message_id": message_id,
            "chunks": len(chunks),
            "size": len(plaintext),
            "plaintext_sha256": pt_hash,
        }

    # -- read paths ----------------------------------------------------------

    def get_plaintext(self, *, request_id: str, message_id: str) -> bytes:
        msg = self._require_message(message_id)
        released = self._store.get_released(message_id)
        if msg["status"] != "released" or released is None:
            self._audit.record(
                request_id=request_id,
                message_id=message_id,
                event="plaintext_denied",
                decision=REJECT,
                reason="plaintext requested before successful finalization",
                detail={"status": msg["status"]},
            )
            raise MessageStateConflict(
                "plaintext is only available after the whole stream"
                " has been authenticated",
                message_id=message_id,
                status=msg["status"],
            )
        return released

    def get_status(self, message_id: str) -> dict:
        msg = self._require_message(message_id)
        chunks = self._store.list_chunks(message_id)
        return {
            "message_id": message_id,
            "status": msg["status"],
            "received_seqs": [c["seq"] for c in chunks],
            "final_seqs": [c["seq"] for c in chunks if c["final"]],
            "chunk_count": msg["chunk_count"],
            "plaintext_sha256": msg["plaintext_sha256"],
            "fail_reason": msg["fail_reason"],
        }
