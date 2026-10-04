"""Core service: randomized encryption writes + keyed blind-index queries.

Query pipeline (the correctness-critical path):
  1. normalize the query value under the field type registered for the purpose
  2. compute the blind index under EVERY index key version currently in use
     (one version normally, old+new during key rotation)
  3. collect candidate record_ids from the index table
  4. DECRYPT each candidate and compare canonical plaintexts — blind-index
     collisions (forced by short index lengths, or accidental) are filtered
     here; candidates that fail to decrypt are reported as ``uncertain``
     instead of being silently counted either way
"""
from __future__ import annotations

import os

from .audit.auditlog import AuditLog
from .config import Settings
from .crypto import blind_index, envelope
from .crypto.keystore import KeyStore
from .errors import (
    NullNotIndexableError,
    PlaintextReadDisabledError,
    RecordNotFoundError,
    RotationConflictError,
    UnknownPurposeError,
)
from .protocol import framing, normalize
from .state import db as db_module
from .state.repository import Repository
from .state.rotation import ROTATING, RotationManager

EQUALITY_LEAKAGE_NOTICE = (
    "blind indexes are deterministic and leak equality between records; "
    "they are a search aid, not anonymization and not encryption"
)


class SensitiveService:
    def __init__(self, settings: Settings, database, audit: AuditLog) -> None:
        self.settings = settings
        self.repo = Repository(database)
        self.keystore = KeyStore(database)
        self.rotation = RotationManager(self.repo)
        self.audit = audit
        self.keystore.ensure_seeded(settings.seed_keys)
        if self.repo.get_meta("active_enc_version") is None:
            self.repo.set_meta("active_enc_version", str(settings.active_enc_version))
        if self.repo.get_meta("active_index_version") is None:
            self.repo.set_meta("active_index_version", str(settings.active_index_version))

    @classmethod
    def from_settings(cls, settings: Settings) -> "SensitiveService":
        database = db_module.Database(settings.database_path)
        return cls(settings, database, AuditLog(settings.audit_log_path))

    # --- version helpers ---
    def _active_enc_version(self) -> int:
        return int(self.repo.get_meta("active_enc_version"))

    def _active_index_version(self) -> int:
        return int(self.repo.get_meta("active_index_version"))

    def index_versions_in_use(self) -> list[int]:
        return self.rotation.active_index_versions(self._active_index_version())

    def _field_type(self, purpose: str) -> str:
        try:
            return self.settings.purposes[purpose]
        except KeyError:
            raise UnknownPurposeError(f"purpose not registered: {purpose!r}") from None

    def _blind_index(self, version: int, purpose: str, normalized: str) -> bytes:
        msg = framing.index_message(purpose, self.settings.norm_version, normalized)
        return blind_index.compute_index(
            self.keystore.get("index", version), msg, self.settings.index_bits)

    # --- writes ---
    def put_record(self, *, request_id: str, record_id: str, field: str,
                   purpose: str, value: str | None) -> dict:
        field_type = self._field_type(purpose)
        if value is None:
            # NULL is stored as a NULL ciphertext with NO blind index row:
            # NULLs are deliberately not searchable.
            self.repo.upsert_record(record_id, field, purpose, None, None)
            self.repo.delete_indexes(record_id, field, purpose)
            trace = ["value=null", "ciphertext=null", "blind-index=removed"]
            result = {"record_id": record_id, "is_null": True,
                      "enc_key_version": None, "index_versions_written": []}
        else:
            normalized = normalize.normalize(value, field_type)
            enc_version = self._active_enc_version()
            aad = framing.aad_message(record_id, field, purpose)
            ciphertext = envelope.encrypt(
                self.keystore.get("enc", enc_version),
                normalized.encode("utf-8"), aad)
            self.repo.upsert_record(
                record_id, field, purpose, ciphertext, enc_version)
            versions = self.index_versions_in_use()
            for version in versions:
                self.repo.put_index(record_id, field, purpose, version,
                                    self._blind_index(version, purpose, normalized))
            trace = [
                "normalized",
                f"encrypted=AES-256-GCM:key-v{enc_version}",
                f"blind-index=HMAC-SHA256/{self.settings.index_bits}b:key-v{versions}",
            ]
            result = {"record_id": record_id, "is_null": False,
                      "enc_key_version": enc_version,
                      "index_versions_written": versions}
        self.audit.log(request_id=request_id, action="put_record",
                       record_id=record_id, field=field, purpose=purpose,
                       outcome="ok")
        result["trace"] = trace
        return result

    # --- reads ---
    def query(self, *, request_id: str, field: str, purpose: str,
              value: str | None) -> dict:
        field_type = self._field_type(purpose)
        if value is None:
            self.audit.log(request_id=request_id, action="query", field=field,
                           purpose=purpose, outcome="error",
                           reason="null_not_indexable")
            raise NullNotIndexableError(
                "NULL values carry no blind index and cannot be queried")
        normalized = normalize.normalize(value, field_type)
        versions = self.index_versions_in_use()
        versioned = [(v, self._blind_index(v, purpose, normalized)) for v in versions]
        candidates = self.repo.find_candidates(field, purpose, versioned)

        confirmed: list[str] = []
        uncertain: list[dict] = []
        filtered = 0
        for rid in candidates:
            row = self.repo.get_record(rid, field, purpose)
            if row is None or row["ciphertext"] is None:
                filtered += 1  # stale index row with no readable record
                continue
            aad = framing.aad_message(rid, field, purpose)
            try:
                plaintext = envelope.decrypt(
                    self.keystore.get("enc", row["enc_key_version"]),
                    row["ciphertext"], aad)
            except envelope.DecryptionError:
                uncertain.append({"record_id": rid, "reason": "decrypt_failed"})
                continue
            if plaintext.decode("utf-8") == normalized:
                confirmed.append(rid)
            else:
                filtered += 1  # blind-index collision, rejected on confirmation
        confirmed.sort()

        counts = {"candidates": len(candidates), "confirmed": len(confirmed),
                  "filtered": filtered, "uncertain": len(uncertain)}
        self.audit.log(request_id=request_id, action="query", field=field,
                       purpose=purpose, index_key_versions=versions,
                       outcome="ok", counts=counts, record_ids=confirmed)
        return {
            "confirmed": confirmed,
            "filtered_candidates": filtered,
            "uncertain": uncertain,
            "index_versions_queried": versions,
            "notice": EQUALITY_LEAKAGE_NOTICE,
            "trace": [
                "normalized",
                f"index-versions-queried={versions}",
                f"candidates={len(candidates)}",
                f"decrypt-confirm: confirmed={len(confirmed)}"
                f" filtered={filtered} uncertain={len(uncertain)}",
            ],
        }

    def get_record(self, *, request_id: str, record_id: str, field: str,
                   purpose: str, include_plaintext: bool = False) -> dict:
        self._field_type(purpose)
        row = self.repo.get_record(record_id, field, purpose)
        if row is None:
            raise RecordNotFoundError(
                f"no record {record_id!r} for {field}/{purpose}")
        result = {
            "record_id": record_id,
            "field": field,
            "purpose": purpose,
            "is_null": row["ciphertext"] is None,
            "enc_key_version": row["enc_key_version"],
            "index_versions": [r["index_key_version"]
                               for r in self.repo.index_rows(record_id, field, purpose)],
        }
        if include_plaintext:
            if not self.settings.allow_plaintext_read:
                raise PlaintextReadDisabledError(
                    "plaintext readback is disabled by configuration")
            if row["ciphertext"] is None:
                result["value"] = None
            else:
                aad = framing.aad_message(record_id, field, purpose)
                result["value"] = envelope.decrypt(
                    self.keystore.get("enc", row["enc_key_version"]),
                    row["ciphertext"], aad).decode("utf-8")
        self.audit.log(request_id=request_id, action="get_record",
                       record_id=record_id, field=field, purpose=purpose,
                       outcome="ok")
        return result

    # --- index-key rotation ---
    def begin_rotation(self, *, request_id: str,
                       crash_after: int | None = None) -> dict:
        if self.rotation.status()["status"] == ROTATING:
            raise RotationConflictError(
                "an index-key rotation is already in progress")
        old_version = self._active_index_version()
        new_version = self.keystore.add("index", os.urandom(32))
        total = len(self.repo.iter_records())
        self.rotation.begin(old_version, new_version, total=total)
        self.audit.log(request_id=request_id, action="rotation_begin",
                       index_key_versions=[old_version, new_version],
                       outcome="ok", counts={"total": total})
        return self._reindex(request_id=request_id, crash_after=crash_after)

    def resume_rotation(self, *, request_id: str) -> dict:
        if self.rotation.status()["status"] != ROTATING:
            raise RotationConflictError("no rotation in progress to resume")
        return self._reindex(request_id=request_id, crash_after=None)

    def rotation_status(self) -> dict:
        return {"rotation": self.rotation.status(),
                "active_index_version": self._active_index_version(),
                "query_index_versions": self.index_versions_in_use()}

    def _reindex(self, *, request_id: str, crash_after: int | None) -> dict:
        state = self.rotation.status()
        old_version, new_version = state["old_version"], state["new_version"]
        processed = 0
        interrupted = False
        for row in self.repo.iter_records():
            if row["ciphertext"] is not None:
                existing = {r["index_key_version"] for r in self.repo.index_rows(
                    row["record_id"], row["field"], row["purpose"])}
                if new_version not in existing:
                    # The stored plaintext is already the canonical normalized
                    # form, so the new index can be recomputed from it directly.
                    aad = framing.aad_message(
                        row["record_id"], row["field"], row["purpose"])
                    normalized = envelope.decrypt(
                        self.keystore.get("enc", row["enc_key_version"]),
                        row["ciphertext"], aad).decode("utf-8")
                    self.repo.put_index(
                        row["record_id"], row["field"], row["purpose"],
                        new_version,
                        self._blind_index(new_version, row["purpose"], normalized))
            processed += 1
            if crash_after is not None and processed >= crash_after:
                interrupted = True  # simulated crash/interruption (test hook)
                break
        self.rotation.advance(processed)
        if interrupted:
            self.audit.log(request_id=request_id, action="rotation_interrupted",
                           index_key_versions=[old_version, new_version],
                           outcome="interrupted",
                           counts={"processed": processed, "total": state["total"]})
        else:
            removed = self.repo.delete_index_version(old_version)
            self.rotation.finish()
            self.repo.set_meta("active_index_version", str(new_version))
            self.audit.log(request_id=request_id, action="rotation_complete",
                           index_key_versions=[old_version, new_version],
                           outcome="ok",
                           counts={"reindexed": processed,
                                   "old_indexes_removed": removed})
        return self.rotation_status()

    # --- dev introspection ---
    def inspect_indexes(self, *, record_id: str, field: str, purpose: str) -> dict:
        rows = self.repo.index_rows(record_id, field, purpose)
        return {
            "record_id": record_id,
            "field": field,
            "purpose": purpose,
            "indexes": [{"index_key_version": r["index_key_version"],
                         "index_hex": bytes(r["index_value"]).hex()}
                        for r in rows],
        }
