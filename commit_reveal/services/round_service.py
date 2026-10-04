"""Round service: the commit-reveal state machine and rule enforcement.

Lifecycle of a round:

    OPEN  --commit deadline passes-->  FROZEN  --finalize-->  FINALIZED

* OPEN:       commitments accepted; reveals rejected (commit set not frozen).
* FROZEN:     commit set immutable; reveals accepted until the reveal
              deadline; finalization possible once the reveal deadline has
              passed or every committed participant has revealed.
* FINALIZED:  terminal. Seed, draw, and public evidence are persisted;
              further commits/reveals are rejected.

Unrevealed commitments at finalization are *excluded* from both the seed and
the candidate set. This is a deliberate, documented trade-off: it makes
selective abort possible (a last-mover who dislikes the impending outcome can
withhold their reveal and change the winner set), which is why this protocol
must not be used for real gambling or prize draws — see README.

The clock is injected so tests can drive the lifecycle deterministically.
"""

from __future__ import annotations

import re
from typing import Callable

from commit_reveal.crypto.commitment import combine_seed, compute_commitment
from commit_reveal.crypto.draw import DRAW_ALGORITHM, draw_index
from commit_reveal.errors import ErrorCode, ProtocolError
from commit_reveal.protocol.encoding import EVIDENCE_VERSION
from commit_reveal.state.audit import AuditEvent, AuditLog
from commit_reveal.state.repository import (
    CommitmentRow,
    Repository,
    ResultRow,
    RevealRow,
    RoundRow,
)

STATUS_OPEN = "OPEN"
STATUS_FROZEN = "FROZEN"
STATUS_FINALIZED = "FINALIZED"

_HEX_RE = re.compile(r"^[0-9a-f]+$")
_COMMITMENT_HEX_LEN = 64  # SHA-256 hex
_MIN_SECRET_BYTES = 16  # 128-bit minimum for random value and salt


class RoundService:
    def __init__(
        self,
        repo: Repository,
        audit: AuditLog,
        clock: Callable[[], int],
    ):
        self._repo = repo
        self._audit = audit
        self._now = clock

    # ------------------------------------------------------------------ util
    def _record(
        self,
        request_id: str,
        round_id: str | None,
        event_type: str,
        outcome: str,
        reason: str,
        detail: dict,
    ) -> None:
        self._audit.record(
            AuditEvent(
                ts=self._now(),
                request_id=request_id,
                round_id=round_id,
                event_type=event_type,
                outcome=outcome,
                reason=reason,
                detail=detail,
            )
        )

    def _reject(
        self,
        request_id: str,
        round_id: str | None,
        event_type: str,
        code: ErrorCode,
        reason: str,
        detail: dict,
    ) -> ProtocolError:
        self._record(request_id, round_id, event_type, "REJECTED", code.value, detail)
        return ProtocolError(code, reason)

    def _load_round(self, round_id: str, request_id: str, event_type: str) -> RoundRow:
        round_row = self._repo.get_round(round_id)
        if round_row is None:
            raise self._reject(
                request_id,
                round_id,
                event_type,
                ErrorCode.ROUND_NOT_FOUND,
                f"round {round_id!r} does not exist",
                {"round_id": round_id},
            )
        return round_row

    def _refresh_status(self, round_row: RoundRow) -> RoundRow:
        """Lazily advance OPEN -> FROZEN once the commit deadline has passed."""
        if round_row.status == STATUS_OPEN and self._now() >= round_row.commit_deadline:
            self._repo.update_round_status(round_row.round_id, STATUS_FROZEN)
            return RoundRow(
                round_id=round_row.round_id,
                participants=round_row.participants,
                commit_deadline=round_row.commit_deadline,
                reveal_deadline=round_row.reveal_deadline,
                status=STATUS_FROZEN,
                created_at=round_row.created_at,
            )
        return round_row

    # -------------------------------------------------------------- use cases
    def create_round(
        self,
        request_id: str,
        round_id: str,
        participants: list[str],
        commit_deadline: int,
        reveal_deadline: int,
    ) -> RoundRow:
        detail = {
            "round_id": round_id,
            "participants": participants,
            "commit_deadline": commit_deadline,
            "reveal_deadline": reveal_deadline,
        }
        problems = []
        if not round_id:
            problems.append("round_id must be non-empty")
        if len(participants) < 2:
            problems.append("at least 2 participants are required")
        if len(set(participants)) != len(participants):
            problems.append("participant ids must be unique")
        if not commit_deadline < reveal_deadline:
            problems.append("commit_deadline must be before reveal_deadline")
        if commit_deadline <= self._now():
            problems.append("commit_deadline must be in the future")
        if self._repo.get_round(round_id) is not None:
            problems.append(f"round {round_id!r} already exists")
        if problems:
            raise self._reject(
                request_id,
                round_id,
                "create_round",
                ErrorCode.INVALID_ROUND_CONFIG,
                "; ".join(problems),
                detail,
            )
        self._repo.insert_round(
            round_id, participants, commit_deadline, reveal_deadline,
            STATUS_OPEN, self._now(),
        )
        self._record(request_id, round_id, "create_round", "ACCEPTED", "round created", detail)
        return self._repo.get_round(round_id)  # type: ignore[return-value]

    def commit(
        self,
        request_id: str,
        round_id: str,
        participant_id: str,
        commitment: str,
    ) -> None:
        round_row = self._refresh_status(self._load_round(round_id, request_id, "commit"))
        detail = {
            "participant_id": participant_id,
            "commitment": commitment,
            "round_status": round_row.status,
        }
        if round_row.status == STATUS_FINALIZED:
            raise self._reject(request_id, round_id, "commit", ErrorCode.ROUND_NOT_OPEN,
                               "round is finalized", detail)
        if round_row.status == STATUS_FROZEN:
            raise self._reject(request_id, round_id, "commit", ErrorCode.LATE_COMMIT,
                               "commit deadline has passed; commit set is frozen", detail)
        if participant_id not in round_row.participants:
            raise self._reject(request_id, round_id, "commit", ErrorCode.UNKNOWN_PARTICIPANT,
                               "participant is not registered for this round", detail)
        if not (_HEX_RE.match(commitment) and len(commitment) == _COMMITMENT_HEX_LEN):
            raise self._reject(request_id, round_id, "commit", ErrorCode.MALFORMED_COMMITMENT,
                               "commitment must be 64 lowercase hex chars (SHA-256)", detail)
        if self._repo.get_commitment(round_id, participant_id) is not None:
            raise self._reject(request_id, round_id, "commit", ErrorCode.DUPLICATE_COMMIT,
                               "participant already committed; commitments are binding", detail)
        self._repo.insert_commitment(
            CommitmentRow(round_id, participant_id, commitment, self._now())
        )
        self._record(request_id, round_id, "commit", "ACCEPTED", "commitment recorded", detail)

    def reveal(
        self,
        request_id: str,
        round_id: str,
        participant_id: str,
        random_value: str,
        salt: str,
    ) -> None:
        round_row = self._refresh_status(self._load_round(round_id, request_id, "reveal"))
        # Secrets appear in the audit detail only as fingerprints (see AuditLog).
        detail = {
            "participant_id": participant_id,
            "random_value": random_value,
            "salt": salt,
            "round_status": round_row.status,
        }
        if round_row.status == STATUS_OPEN:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.REVEAL_BEFORE_FREEZE,
                               "commit set is not frozen yet; reveals open after the "
                               "commit deadline", detail)
        if round_row.status == STATUS_FINALIZED:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.ROUND_FINALIZED,
                               "round is finalized; result is immutable", detail)
        if self._now() > round_row.reveal_deadline:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.LATE_REVEAL,
                               "reveal deadline has passed", detail)
        if not (_HEX_RE.match(random_value) and _HEX_RE.match(salt)
                and len(random_value) >= 2 * _MIN_SECRET_BYTES
                and len(salt) >= 2 * _MIN_SECRET_BYTES):
            raise self._reject(request_id, round_id, "reveal", ErrorCode.MALFORMED_REVEAL,
                               "random_value and salt must be hex of at least "
                               f"{_MIN_SECRET_BYTES} bytes each", detail)
        commitment_row = self._repo.get_commitment(round_id, participant_id)
        if commitment_row is None:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.UNKNOWN_COMMITMENT,
                               "no commitment on record for this participant", detail)
        if self._repo.get_reveal(round_id, participant_id) is not None:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.DUPLICATE_REVEAL,
                               "participant already revealed; a reveal counts exactly once",
                               detail)
        expected = compute_commitment(
            round_id, participant_id, bytes.fromhex(random_value), bytes.fromhex(salt)
        )
        if expected != commitment_row.commitment:
            raise self._reject(request_id, round_id, "reveal", ErrorCode.COMMITMENT_MISMATCH,
                               "recomputed commitment does not match the recorded "
                               "commitment (wrong random value or salt)", detail)
        self._repo.insert_reveal(
            RevealRow(round_id, participant_id, random_value, salt, self._now())
        )
        self._record(request_id, round_id, "reveal", "ACCEPTED",
                     "reveal verified against commitment", detail)

    def finalize(self, request_id: str, round_id: str) -> dict:
        round_row = self._refresh_status(self._load_round(round_id, request_id, "finalize"))
        if round_row.status == STATUS_FINALIZED:
            raise self._reject(
                request_id, round_id, "finalize", ErrorCode.ROUND_FINALIZED,
                "round is already finalized", {"round_status": round_row.status},
            )
        if round_row.status == STATUS_OPEN:
            raise self._reject(request_id, round_id, "finalize",
                               ErrorCode.ROUND_NOT_FINALIZABLE,
                               "commit phase is still open", {"round_status": round_row.status})
        commitments = self._repo.list_commitments(round_id)
        reveals = self._repo.list_reveals(round_id)
        all_revealed = len(reveals) == len(commitments) and len(commitments) > 0
        deadline_passed = self._now() >= round_row.reveal_deadline
        if not (all_revealed or deadline_passed):
            raise self._reject(
                request_id, round_id, "finalize", ErrorCode.ROUND_NOT_FINALIZABLE,
                "reveal deadline has not passed and not all committed "
                "participants have revealed",
                {"round_status": round_row.status,
                 "committed": len(commitments), "revealed": len(reveals)},
            )
        if not reveals:
            self._record(request_id, round_id, "finalize", "UNDETERMINED",
                         "no valid reveals; outcome cannot be determined",
                         {"committed": len(commitments)})
            raise ProtocolError(
                ErrorCode.NO_VALID_REVEALS,
                "no participant revealed; the round has no determinate outcome",
            )

        eligible = sorted(r.participant_id for r in reveals)
        seed = combine_seed(
            round_id, [(r.participant_id, bytes.fromhex(r.random_value)) for r in reveals]
        )
        winner_index = draw_index(seed, len(eligible))
        winner = eligible[winner_index]
        revealed_pids = {r.participant_id for r in reveals}
        unrevealed = sorted(
            c.participant_id for c in commitments if c.participant_id not in revealed_pids
        )
        evidence = {
            "version": EVIDENCE_VERSION,
            "round_id": round_id,
            "participants": round_row.participants,
            "commit_deadline": round_row.commit_deadline,
            "reveal_deadline": round_row.reveal_deadline,
            "commitments": [
                {"participant_id": c.participant_id, "commitment": c.commitment}
                for c in commitments
            ],
            "reveals": [
                {"participant_id": r.participant_id,
                 "random_value": r.random_value, "salt": r.salt}
                for r in reveals
            ],
            "unrevealed_commitments": unrevealed,
            "seed": seed,
            "draw": {
                "algorithm": DRAW_ALGORITHM,
                "eligible": eligible,
                "winner_index": winner_index,
                "winner": winner,
            },
        }
        self._repo.insert_result(
            ResultRow(round_id, seed, winner, evidence, self._now())
        )
        self._repo.update_round_status(round_id, STATUS_FINALIZED)
        self._record(
            request_id, round_id, "finalize", "ACCEPTED",
            f"round finalized; winner={winner}",
            {"seed": seed, "winner": winner, "eligible": eligible,
             "unrevealed_commitments": unrevealed},
        )
        return evidence

    # ----------------------------------------------------------------- reads
    def get_round_view(self, round_id: str) -> dict:
        round_row = self._refresh_status(self._load_round(round_id, "read", "get_round"))
        commitments = self._repo.list_commitments(round_id)
        reveals = self._repo.list_reveals(round_id)
        result = self._repo.get_result(round_id)
        return {
            "round_id": round_row.round_id,
            "status": round_row.status,
            "participants": round_row.participants,
            "commit_deadline": round_row.commit_deadline,
            "reveal_deadline": round_row.reveal_deadline,
            "committed": sorted(c.participant_id for c in commitments),
            "revealed": sorted(r.participant_id for r in reveals),
            "result": (
                {"seed": result.seed, "winner": result.winner,
                 "finalized_at": result.finalized_at}
                if result else None
            ),
        }

    def get_evidence(self, round_id: str) -> dict:
        result = self._repo.get_result(round_id)
        if result is None:
            raise ProtocolError(
                ErrorCode.ROUND_NOT_FINALIZABLE,
                f"round {round_id!r} has no finalized result yet",
            )
        return result.evidence
