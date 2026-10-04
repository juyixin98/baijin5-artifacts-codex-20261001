"""Versioned key storage.

Local dev fixture: keys live in the SQLite file next to the data they protect.
That is acceptable ONLY for this local synthetic setup — production must source
keys from a KMS/HSM and never co-locate key material with ciphertexts.
"""
from __future__ import annotations


class KeyStore:
    def __init__(self, db) -> None:
        self._db = db

    def ensure_seeded(self, seed: dict[str, dict[int, bytes]]) -> None:
        with self._db.lock:
            for kind, versions in seed.items():
                for version, key in versions.items():
                    self._db.conn.execute(
                        "INSERT OR IGNORE INTO keys(kind, version, key_bytes)"
                        " VALUES (?,?,?)",
                        (kind, version, key),
                    )
            self._db.conn.commit()

    def get(self, kind: str, version: int) -> bytes:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT key_bytes FROM keys WHERE kind=? AND version=?",
                (kind, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"no {kind} key for version {version}")
        return bytes(row[0])

    def versions(self, kind: str) -> list[int]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT version FROM keys WHERE kind=? ORDER BY version", (kind,)
            ).fetchall()
        return [int(r[0]) for r in rows]

    def add(self, kind: str, key: bytes) -> int:
        with self._db.lock:
            existing = self.versions(kind)
            version = (max(existing) + 1) if existing else 1
            self._db.conn.execute(
                "INSERT INTO keys(kind, version, key_bytes) VALUES (?,?,?)",
                (kind, version, key),
            )
            self._db.conn.commit()
        return version
