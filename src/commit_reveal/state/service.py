"""Round state machine: commit -> freeze -> reveal -> finalize.

Phase boundaries are driven by the injected clock:

    now <  commit_deadline                    commitments accepted
    commit_deadline <= now < reveal_deadline  commitment set frozen, reveals accepted
    reveal_deadline <= now                    finalizable

Rules enforced here:
- a commitment binds (round, participant); one per participant, no amendments;
- a reveal must match the stored commitment and is counted at most once;
- unrevealed commitments are excluded from the seed at finalize time and are
  flagged in the evidence as a bias risk (selective abort).
"""

from __future__ import annotations

import uuid

from commit_reveal import clock as clock_mod
from commit_reveal.clock import Clock
from commit_reveal.crypto import commitment as commit_mod
from commit_reveal.crypto import seed as seed_mod
from commit_reveal.draw.deterministic import deterministic_ranking
from commit_reveal.protocol import encoding, errors
from commit_reveal.state import store as store_mod
from commit_reveal.state.audit import Audit, fingerprint
from commit_reveal.state.store import Store

HEX64 = "commitment/value/salt must be 64 lowercase hex chars (32 bytes)"


def _require_hex64(name: str, text: str) -> None:
    if len(text) != encoding.COMMITMENT_HEX_LEN:
        raise errors.ProtocolError(f"{name}: {HEX64}", field=name)
    try:
        bytes.fromhex(text)
    except ValueError as exc:
        raise errors.ProtocolError(f"{name}: not valid hex", field=name) from exc


class RoundService:
    def __init__(self, store: Store, clock: Clock, audit: Audit) -> None:
        self._store = store
        self._clock = clock
        self._audit = audit

    # -- round creation ---------------------------------------------------
    def create_round(
        self,
        participants: list[str],
        commit_deadline: str,
        reveal_deadline: str,
        min_reveals: int,
        request_id: str,
        round_id: str | None = None,
    ) -> dict:
        round_id = round_id or uuid.uuid4().hex
        if self._store.get_round(round_id) is not None:
            raise errors.ProtocolError("round id already exists", round_id=round_id)
        if len(set(participants)) != len(participants) or len(participants) < 2:
            raise errors.ProtocolError(
                "participants must be unique and at least two",
                round_id=round_id,
            )
        commit_at = clock_mod.from_iso(commit_deadline)
        reveal_at = clock_mod.from_iso(reveal_deadline)
        if not commit_at < reveal_at:
            raise errors.ProtocolError(
                "commit_deadline must be before reveal_deadline", round_id=round_id
            )
        if not 1 <= min_reveals <= len(participants):
            raise errors.ProtocolError(
                "min_reveals out of range", round_id=round_id
            )
        self._store.create_round(
            round_id=round_id,
            participants=sorted(participants),
            commit_deadline=clock_mod.to_iso(commit_at),
            reveal_deadline=clock_mod.to_iso(reveal_at),
            min_reveals=min_reveals,
            created_at=clock_mod.to_iso(self._clock.now()),
        )
        self._audit.record(
            request_id, "round.create", "ACCEPT",
            f"round created with {len(participants)} participants",
            round_id=round_id,
        )
        return self._store.get_round(round_id)  # type: ignore[return-value]

    # -- commit phase -------------------------------------------------------
    def commit(
        self, round_id: str, participant_id: str, commitment: str, request_id: str
    ) -> None:
        _require_hex64("commitment", commitment)
        rnd = self._round(round_id)
        self._require_participant(rnd, participant_id, request_id, "commit")
        now = self._clock.now()
        if now >= clock_mod.from_iso(rnd["commit_deadline"]):
            self._audit.record(
                request_id, "commit", "REJECT",
                "commitment arrived after commit deadline; set already frozen",
                round_id, participant_id,
                detail={"deadline": rnd["commit_deadline"]},
            )
            raise errors.LateCommitmentError(
                "commit phase is closed", round_id=round_id
            )
        if self._store.get_commitment(round_id, participant_id) is not None:
            self._audit.record(
                request_id, "commit", "REJECT",
                "participant already committed; amendments not allowed",
                round_id, participant_id,
            )
            raise errors.DuplicateCommitmentError(
                "commitment already recorded for this participant",
                round_id=round_id,
            )
        self._store.insert_commitment(
            round_id, participant_id, commitment, clock_mod.to_iso(now)
        )
        self._audit.record(
            request_id, "commit", "ACCEPT",
            "commitment recorded within commit window",
            round_id, participant_id,
            detail={"commitment_prefix": commitment[:12]},
        )

    # -- reveal phase -------------------------------------------------------
    def reveal(
        self,
        round_id: str,
        participant_id: str,
        value_hex: str,
        salt_hex: str,
        request_id: str,
    ) -> None:
        _require_hex64("value", value_hex)
        _require_hex64("salt", salt_hex)
        rnd = self._round(round_id)
        self._require_participant(rnd, participant_id, request_id, "reveal")
        now = self._clock.now()
        if now < clock_mod.from_iso(rnd["commit_deadline"]):
            self._audit.record(
                request_id, "reveal", "REJECT",
                "reveal phase not open yet; commitment set not frozen",
                round_id, participant_id,
            )
            raise errors.RevealPhaseNotOpenError(
                "reveal phase has not started", round_id=round_id
            )
        if now >= clock_mod.from_iso(rnd["reveal_deadline"]):
            self._audit.record(
                request_id, "reveal", "REJECT",
                "reveal arrived after reveal deadline",
                round_id, participant_id,
                detail={"deadline": rnd["reveal_deadline"]},
            )
            raise errors.LateRevealError("reveal phase is closed", round_id=round_id)
        stored = self._store.get_commitment(round_id, participant_id)
        if stored is None:
            self._audit.record(
                request_id, "reveal", "REJECT",
                "no commitment on file for participant",
                round_id, participant_id,
            )
            raise errors.NoCommitmentError(
                "cannot reveal without a prior commitment", round_id=round_id
            )
        if self._store.get_reveal(round_id, participant_id) is not None:
            self._audit.record(
                request_id, "reveal", "REJECT",
                "reveal already counted; duplicate reveals are ignored",
                round_id, participant_id,
            )
            raise errors.DuplicateRevealError(
                "reveal already recorded for this participant", round_id=round_id
            )
        recomputed = commit_mod.compute_commitment(
            round_id,
            participant_id,
            bytes.fromhex(value_hex),
            bytes.fromhex(salt_hex),
        )
        if not commit_mod.constant_time_equal(recomputed, stored):
            self._audit.record(
                request_id, "reveal", "REJECT",
                "recomputed commitment does not match stored commitment",
                round_id, participant_id,
                detail={
                    "value_fp": fingerprint(value_hex),
                    "salt_fp": fingerprint(salt_hex),
                },
            )
            raise errors.CommitmentMismatchError(
                "value/salt do not match the commitment", round_id=round_id
            )
        self._store.insert_reveal(
            round_id, participant_id, value_hex, salt_hex, clock_mod.to_iso(now)
        )
        self._audit.record(
            request_id, "reveal", "ACCEPT",
            "reveal verified against commitment and recorded",
            round_id, participant_id,
            detail={"value_fp": fingerprint(value_hex)},
        )

    # -- finalize -----------------------------------------------------------
    def finalize(self, round_id: str, request_id: str) -> dict:
        rnd = self._round(round_id)
        existing = self._store.get_result(round_id)
        if existing is not None:
            self._audit.record(
                request_id, "finalize", "ACCEPT",
                "result already finalized; returning stored result (idempotent replay)",
                round_id,
            )
            return existing
        now = self._clock.now()
        if now < clock_mod.from_iso(rnd["reveal_deadline"]):
            self._audit.record(
                request_id, "finalize", "REJECT",
                "reveal deadline not reached; cannot finalize",
                round_id,
            )
            raise errors.RoundNotFinalizableError(
                "reveal phase is still open", round_id=round_id
            )

        commitments = self._store.list_commitments(round_id)
        reveals = self._store.list_reveals(round_id)
        revealed_ids = sorted(r["participant_id"] for r in reveals)
        unrevealed = sorted(
            c["participant_id"] for c in commitments
            if c["participant_id"] not in set(revealed_ids)
        )
        set_hash = commit_mod.commitment_set_hash(
            [(c["participant_id"], c["commitment"]) for c in commitments]
        )
        finalized_at = clock_mod.to_iso(now)

        if len(reveals) < rnd["min_reveals"]:
            evidence = self._build_evidence(
                rnd, commitments, reveals, unrevealed, set_hash,
                seed_hex=None, ranking=[], winner=None,
                aborted=True, finalized_at=finalized_at,
            )
            self._store.insert_result(
                round_id, None, None, [], evidence, finalized_at
            )
            self._store.set_status(round_id, store_mod.STATUS_ABORTED)
            self._audit.record(
                request_id, "finalize", "UNDECIDABLE",
                f"only {len(reveals)} reveals (< min_reveals="
                f"{rnd['min_reveals']}); round aborted, no draw possible",
                round_id,
                detail={"revealed": revealed_ids, "unrevealed": unrevealed},
            )
            return self._store.get_result(round_id)  # type: ignore[return-value]

        seed = seed_mod.derive_seed(
            round_id, [bytes.fromhex(r["value_hex"]) for r in reveals]
        )
        ranking = deterministic_ranking(revealed_ids, seed)
        winner = ranking[0]
        evidence = self._build_evidence(
            rnd, commitments, reveals, unrevealed, set_hash,
            seed_hex=seed.hex(), ranking=ranking, winner=winner,
            aborted=False, finalized_at=finalized_at,
        )
        self._store.insert_result(
            round_id, seed.hex(), winner, ranking, evidence, finalized_at
        )
        self._store.set_status(round_id, store_mod.STATUS_FINALIZED)
        self._audit.record(
            request_id, "finalize", "ACCEPT",
            f"draw finalized; winner recorded; "
            f"{len(unrevealed)} commitment(s) unrevealed",
            round_id,
            detail={
                "revealed": revealed_ids,
                "unrevealed": unrevealed,
                "bias_warning": bool(unrevealed),
            },
        )
        return self._store.get_result(round_id)  # type: ignore[return-value]

    # -- queries ------------------------------------------------------------
    def get_round(self, round_id: str) -> dict:
        return self._round(round_id)

    def get_evidence(self, round_id: str) -> dict:
        result = self._store.get_result(round_id)
        if result is None:
            raise errors.RoundNotFinalizableError(
                "round not finalized; no public evidence yet", round_id=round_id
            )
        return result["evidence"]

    def list_audit(self, round_id: str | None = None) -> list[dict]:
        return self._store.list_audit(round_id)

    # -- helpers ------------------------------------------------------------
    def _round(self, round_id: str) -> dict:
        rnd = self._store.get_round(round_id)
        if rnd is None:
            raise errors.RoundNotFoundError("unknown round", round_id=round_id)
        return rnd

    def _require_participant(
        self, rnd: dict, participant_id: str, request_id: str, event: str
    ) -> None:
        if participant_id not in rnd["participants"]:
            self._audit.record(
                request_id, event, "REJECT",
                "participant is not registered for this round",
                rnd["round_id"], participant_id,
            )
            raise errors.UnknownParticipantError(
                "participant not registered for this round",
                round_id=rnd["round_id"],
            )

    @staticmethod
    def _build_evidence(
        rnd: dict,
        commitments: list[dict],
        reveals: list[dict],
        unrevealed: list[str],
        set_hash: str,
        *,
        seed_hex: str | None,
        ranking: list[str],
        winner: str | None,
        aborted: bool,
        finalized_at: str,
    ) -> dict:
        return {
            "protocol": "crp-v1",
            "round_id": rnd["round_id"],
            "participants": rnd["participants"],
            "commit_deadline": rnd["commit_deadline"],
            "reveal_deadline": rnd["reveal_deadline"],
            "min_reveals": rnd["min_reveals"],
            "commitments": [
                {
                    "participant_id": c["participant_id"],
                    "commitment": c["commitment"],
                    "committed_at": c["committed_at"],
                }
                for c in commitments
            ],
            "commitment_set_hash": set_hash,
            "reveals": [
                {
                    "participant_id": r["participant_id"],
                    "value": r["value_hex"],
                    "salt": r["salt_hex"],
                    "revealed_at": r["revealed_at"],
                }
                for r in reveals
            ],
            "unrevealed_commitments": unrevealed,
            "seed": seed_hex,
            "draw": {
                "eligible": sorted(r["participant_id"] for r in reveals),
                "ranking": ranking,
                "winner": winner,
            },
            "aborted": aborted,
            "bias_warning": bool(unrevealed),
            "bias_note": (
                "Unrevealed commitments were excluded from the seed. A "
                "participant who sees others' reveals before the reveal "
                "deadline can selectively abort to exclude their own input, "
                "biasing the outcome. This protocol is NOT suitable for "
                "real-money gambling or high-stakes draws."
                if unrevealed
                else "All commitments were revealed; no selective-abort bias."
            ),
            "finalized_at": finalized_at,
        }
