"""Structured audit log (JSON lines).

PRIVACY INVARIANT: audit entries carry request identity, record identity
(record_id), field/purpose names, key versions and counts ONLY. Values,
normalized forms, index digests, ciphertexts and key material are rejected at
this module's boundary, so a caller cannot accidentally log them — the
whitelist is enforced by raising on any unknown field name.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_KEYS = frozenset({
    "ts", "request_id", "action", "record_id", "record_ids",
    "field", "purpose", "enc_key_version", "index_key_versions",
    "outcome", "counts", "reason",
})


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **fields) -> dict:
        for key in fields:
            if key not in ALLOWED_KEYS:
                raise ValueError(f"audit field not allowed: {key!r}")
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **fields}
        line = json.dumps(entry, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [
            json.loads(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
