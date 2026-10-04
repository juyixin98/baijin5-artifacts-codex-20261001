"""Index-key rotation state machine.

Invariants:
- While status == "rotating", every query and every write uses BOTH the old
  and the new index key version, so a half-reindexed table never misses
  records and never double-reports them (query results are deduplicated by
  record_id and confirmed against decrypted plaintext).
- The state lives in the same SQLite file as the data, so a crash or an
  interrupted run resumes from the persisted (old_version, new_version,
  processed) tuple instead of losing track of the in-flight rotation.
"""
from __future__ import annotations

import json

IDLE = "idle"
ROTATING = "rotating"


class RotationManager:
    def __init__(self, repo) -> None:
        self._repo = repo

    def status(self) -> dict:
        raw = self._repo.get_meta("rotation")
        if raw is None:
            return {"status": IDLE}
        return json.loads(raw)

    def begin(self, old_version: int, new_version: int, total: int) -> dict:
        state = {
            "status": ROTATING,
            "old_version": old_version,
            "new_version": new_version,
            "processed": 0,
            "total": total,
        }
        self._repo.set_meta("rotation", json.dumps(state))
        return state

    def advance(self, processed: int) -> dict:
        state = self.status()
        state["processed"] = processed
        self._repo.set_meta("rotation", json.dumps(state))
        return state

    def finish(self) -> None:
        self._repo.set_meta("rotation", json.dumps({"status": IDLE}))

    def active_index_versions(self, active_version: int) -> list[int]:
        """Versions that queries and writes must both cover right now."""
        state = self.status()
        if state["status"] == ROTATING:
            return sorted({state["old_version"], state["new_version"]})
        return [active_version]
