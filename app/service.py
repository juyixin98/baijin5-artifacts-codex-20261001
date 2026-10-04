"""Aggregation service: batch lifecycle on top of encoding + crypto + storage.

Batch states:  OPEN -> AGGREGATED -> DECRYPTED
                          ^-- aggregate() may be re-run while OPEN;
                              once AGGREGATED the batch is closed to new
                              contributions so the audited result is stable.

Trust assumptions (local test deployment):
  * The server holds the private key, so it plays the decryptor role.
    In a real deployment decryption would be a separate party/threshold.
  * Plaintext range checks happen at the *client* (encoding layer); the
    server cannot inspect encrypted values.  It enforces what it can:
    key binding, coefficient range, ciphertext shape, and a worst-case
    aggregate bound derived from the coefficients.
"""

from __future__ import annotations

import uuid

from . import crypto_adapter
from .audit import AuditLogger
from .config import Settings
from .encoding import (
    EncodingParams,
    check_coefficient,
    decode_signed,
    encode_signed,
    worst_case_contribution,
)
from .errors import ErrorCategory, PaillierServiceError
from .storage import Storage

STATE_OPEN = "OPEN"
STATE_AGGREGATED = "AGGREGATED"
STATE_DECRYPTED = "DECRYPTED"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class AggregationService:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.audit = AuditLogger(storage)

    # --- helpers ---------------------------------------------------------
    def _require_batch(self, batch_id: str) -> dict:
        batch = self.storage.get_batch(batch_id)
        if batch is None:
            raise PaillierServiceError(
                ErrorCategory.BATCH_NOT_FOUND,
                f"unknown batch {batch_id!r}",
                {"batch_id": batch_id},
            )
        return batch

    @staticmethod
    def _public_key_of(batch: dict):
        return crypto_adapter.reconstruct_public_key(int(batch["public_n"]))

    @staticmethod
    def _private_key_of(batch: dict):
        if batch["private_p"] is None:
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                "private key not held by this service instance",
                {"batch_id": batch["batch_id"]},
            )
        public = crypto_adapter.reconstruct_public_key(int(batch["public_n"]))
        return crypto_adapter.reconstruct_private_key(
            public, int(batch["private_p"]), int(batch["private_q"])
        )

    @staticmethod
    def _params_of(batch: dict) -> EncodingParams:
        import json

        return EncodingParams.from_dict(json.loads(batch["encoding_params"]))

    # --- batch lifecycle ---------------------------------------------------
    def create_batch(
        self,
        run_id: str,
        label: str,
        params: EncodingParams | None = None,
        key_size: int | None = None,
    ) -> dict:
        params = params or self.settings.encoding
        key_size = key_size or self.settings.key_size
        keypair = crypto_adapter.generate_keypair(key_size)
        params.validate_against_modulus(keypair.public_key.n)

        batch_id = uuid.uuid4().hex[:16]
        key_id = crypto_adapter.key_fingerprint(keypair.public_key)
        self.storage.insert_batch(
            {
                "batch_id": batch_id,
                "label": label,
                "state": STATE_OPEN,
                "key_id": key_id,
                "public_n": str(keypair.public_key.n),
                # Local test service: the private key lives next to the data.
                "private_p": str(keypair.private_key.p),
                "private_q": str(keypair.private_key.q),
                "encoding_params": params.to_dict(),
                "bound_used": 0,
            }
        )
        self.audit.record(
            run_id,
            "BATCH_CREATED",
            batch_id,
            label=label,
            key_id=key_id,
            key_size=key_size,
            encoding_params=params.to_dict(),
        )
        return self.describe_batch(batch_id)

    def describe_batch(self, batch_id: str) -> dict:
        batch = self._require_batch(batch_id)
        contributions = self.storage.list_contributions(batch_id)
        aggregate = self.storage.get_aggregate(batch_id)
        return {
            "batch_id": batch["batch_id"],
            "label": batch["label"],
            "state": batch["state"],
            "key_id": batch["key_id"],
            "public_key_n": batch["public_n"],
            "encoding_params": self._params_of(batch).to_dict(),
            "bound_used": int(batch["bound_used"]),
            "contribution_count": len(contributions),
            "has_aggregate": aggregate is not None,
        }

    # --- contributions -----------------------------------------------------
    def client_encrypt(self, batch_id: str, plaintext: int) -> int:
        """Client-side helper: encode + encrypt a plaintext for a batch.

        Exposed for local fixtures/demo clients; real participants would run
        the same two calls (encoding.encode_signed + adapter.encrypt_encoded)
        on their own machine against the batch's public key.
        """
        batch = self._require_batch(batch_id)
        params = self._params_of(batch)
        public_key = self._public_key_of(batch)
        encoded = encode_signed(plaintext, public_key.n, params)
        return crypto_adapter.encrypt_encoded(public_key, encoded)

    def submit_contribution(
        self,
        run_id: str,
        batch_id: str,
        participant_id: str,
        key_id: str,
        ciphertext: int,
        coefficient: int,
        plaintext_fixture: int | None = None,
    ) -> dict:
        batch = self._require_batch(batch_id)
        params = self._params_of(batch)

        if batch["state"] != STATE_OPEN:
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                f"batch is {batch['state']}, not OPEN",
                {"batch_id": batch_id, "state": batch["state"]},
            )
        if key_id != batch["key_id"]:
            self.audit.record(
                run_id, "CONTRIBUTION_REJECTED", batch_id,
                participant_id=participant_id, reason="KEY_MISMATCH",
                presented_key_id=key_id,
            )
            raise PaillierServiceError(
                ErrorCategory.KEY_MISMATCH,
                "ciphertext was encrypted under a different key than the "
                "batch key; refusing to mix keys in one aggregate",
                {"expected_key_id": batch["key_id"], "presented_key_id": key_id},
            )

        check_coefficient(coefficient, params)
        public_key = self._public_key_of(batch)
        crypto_adapter.validate_ciphertext(public_key, ciphertext)

        # Worst-case bound: assume every plaintext sits at the range edge.
        bound_used = int(batch["bound_used"])
        added = worst_case_contribution(coefficient, params)
        if bound_used + added > params.max_aggregate_abs:
            self.audit.record(
                run_id, "CONTRIBUTION_REJECTED", batch_id,
                participant_id=participant_id,
                reason="AGGREGATE_BOUND_EXCEEDED",
                bound_used=bound_used, added=added,
                max_aggregate_abs=params.max_aggregate_abs,
            )
            raise PaillierServiceError(
                ErrorCategory.AGGREGATE_BOUND_EXCEEDED,
                "accepting this contribution could push the true aggregate "
                "past max_aggregate_abs, after which modular wraparound "
                "would make the result undecodable",
                {
                    "bound_used": bound_used,
                    "contribution_worst_case": added,
                    "max_aggregate_abs": params.max_aggregate_abs,
                },
            )

        if plaintext_fixture is not None and not self.settings.accept_plaintext_fixtures:
            plaintext_fixture = None

        contribution_id = self.storage.insert_contribution(
            {
                "batch_id": batch_id,
                "participant_id": participant_id,
                "key_id": key_id,
                "ciphertext": ciphertext,
                "coefficient": coefficient,
                "plaintext_fixture": plaintext_fixture,
                "run_id": run_id,
            }
        )
        self.storage.update_bound_used(batch_id, bound_used + added)
        self.audit.record(
            run_id, "CONTRIBUTION_ACCEPTED", batch_id,
            contribution_id=contribution_id,
            participant_id=participant_id,
            coefficient=coefficient,
            bound_used_after=bound_used + added,
        )
        return {
            "contribution_id": contribution_id,
            "batch_id": batch_id,
            "bound_used": bound_used + added,
        }

    # --- aggregation -------------------------------------------------------
    def aggregate(self, run_id: str, batch_id: str) -> dict:
        batch = self._require_batch(batch_id)
        if batch["state"] not in (STATE_OPEN, STATE_AGGREGATED):
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                f"cannot aggregate batch in state {batch['state']}",
                {"batch_id": batch_id, "state": batch["state"]},
            )
        contributions = self.storage.list_contributions(batch_id)
        if not contributions:
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                "cannot aggregate an empty batch",
                {"batch_id": batch_id},
            )

        public_key = self._public_key_of(batch)
        steps = []
        running = None
        for row in contributions:
            ciphertext = int(row["ciphertext"])
            coefficient = int(row["coefficient"])
            weighted = crypto_adapter.multiply_by_scalar(
                public_key, ciphertext, coefficient
            )
            running = (
                weighted
                if running is None
                else crypto_adapter.add_ciphertexts(public_key, running, weighted)
            )
            steps.append(
                {
                    "contribution_id": row["contribution_id"],
                    "participant_id": row["participant_id"],
                    "coefficient": coefficient,
                    "op": "weighted_add",
                }
            )

        self.storage.upsert_aggregate(
            {
                "batch_id": batch_id,
                "result_ciphertext": running,
                "contribution_count": len(contributions),
                "run_id": run_id,
            }
        )
        self.storage.update_batch_state(batch_id, STATE_AGGREGATED)
        self.audit.record(
            run_id, "AGGREGATE_COMPUTED", batch_id,
            contribution_count=len(contributions), steps=steps,
        )
        return {
            "batch_id": batch_id,
            "result_ciphertext": str(running),
            "contribution_count": len(contributions),
            "steps": steps,
        }

    # --- decryption --------------------------------------------------------
    def decrypt_result(self, run_id: str, batch_id: str) -> dict:
        batch = self._require_batch(batch_id)
        aggregate = self.storage.get_aggregate(batch_id)
        if aggregate is None:
            raise PaillierServiceError(
                ErrorCategory.BATCH_STATE_INVALID,
                "batch has no aggregate; run aggregate first",
                {"batch_id": batch_id},
            )
        params = self._params_of(batch)
        private_key = self._private_key_of(batch)
        public_key = self._public_key_of(batch)

        raw = crypto_adapter.decrypt_to_encoded(
            private_key, int(aggregate["result_ciphertext"])
        )
        try:
            plaintext = decode_signed(raw, public_key.n, params)
        except PaillierServiceError as exc:
            # Wraparound / out-of-range is recorded as a failure, never as a value.
            self.audit.record(
                run_id, "DECODE_FAILED", batch_id,
                category=exc.category.value, message=exc.message,
            )
            raise

        self.storage.mark_aggregate_decrypted(batch_id, plaintext)
        self.storage.update_batch_state(batch_id, STATE_DECRYPTED)
        self.audit.record(
            run_id, "RESULT_DECRYPTED", batch_id,
            raw_residue=raw, plaintext=plaintext,
        )
        return {
            "batch_id": batch_id,
            "plaintext": plaintext,
            "raw_residue": str(raw),
            "contribution_count": aggregate["contribution_count"],
        }
