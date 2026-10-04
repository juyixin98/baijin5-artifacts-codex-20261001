"""Salted SCRAM verifier material and its SQLite-backed repository.

The server stores only derived material — never the password or the
SaltedPassword::

    StoredKey = H(HMAC(SaltedPassword, "Client Key"))
    ServerKey = HMAC(SaltedPassword, "Server Key")

plus the per-account salt and iteration count.  Knowledge of StoredKey and
ServerKey is sufficient to run the server side but does not yield the
ClientKey (a preimage of StoredKey under SHA-256) or the password.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from . import crypto
from .saslprep import sasl_prep

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scram_verifiers (
    username        TEXT PRIMARY KEY,
    salt            BLOB NOT NULL,
    iteration_count INTEGER NOT NULL,
    stored_key      BLOB NOT NULL,
    server_key      BLOB NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CONSTRAINT len_check CHECK (
        length(stored_key) = 32 AND length(server_key) = 32 AND length(salt) >= 16
    )
);
"""


@dataclass(frozen=True)
class Verifier:
    username: str
    salt: bytes
    iteration_count: int
    stored_key: bytes
    server_key: bytes

    def redacted(self) -> dict:
        """Structural view for logs: key material is never exposed."""
        return {
            "username": self.username,
            "salt_b64_len": len(self.salt),
            "iteration_count": self.iteration_count,
            "stored_key_alg": "SHA-256",
            "server_key_alg": "HMAC-SHA-256",
        }


def build_verifier(password: str, *, salt: bytes | None = None, iterations: int = 4096, salt_bytes: int = 32) -> Verifier:
    """Derive a fresh :class:`Verifier` for provisioning local test accounts.

    The plaintext password is used only inside this call; it never leaves the
    function and the returned record contains neither it nor SaltedPassword.
    """
    if iterations < 4096:
        raise ValueError("refusing to build verifier below RFC 7677 minimum of 4096 iterations")
    prepared = sasl_prep(password).encode("utf-8")
    actual_salt = salt if salt is not None else crypto.random_salt(salt_bytes)
    salted = crypto.derive_salted_password(prepared, actual_salt, iterations)
    ck = crypto.client_key(salted)
    stored = crypto.stored_key_from_client_key(ck)
    server = crypto.server_key(salted)
    return Verifier(username="", salt=actual_salt, iteration_count=iterations, stored_key=stored, server_key=server)


class VerifierRepository:
    """Thread-safe SQLite repository holding salted verifiers only."""

    def __init__(self, path: str) -> None:
        # check_same_thread=False + a lock: FastAPI sync endpoints run in a
        # thread pool, so the connection is shared under mutual exclusion.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def upsert(self, username: str, verifier: Verifier) -> None:
        prepared_name = sasl_prep(username)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO scram_verifiers(username, salt, iteration_count, stored_key, server_key)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    salt=excluded.salt,
                    iteration_count=excluded.iteration_count,
                    stored_key=excluded.stored_key,
                    server_key=excluded.server_key,
                    created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (prepared_name, verifier.salt, verifier.iteration_count, verifier.stored_key, verifier.server_key),
            )
            self._conn.commit()

    def get(self, username: str) -> Verifier | None:
        prepared_name = sasl_prep(username)
        with self._lock:
            row = self._conn.execute(
                "SELECT username, salt, iteration_count, stored_key, server_key "
                "FROM scram_verifiers WHERE username = ?",
                (prepared_name,),
            ).fetchone()
        if row is None:
            return None
        return Verifier(
            username=row[0], salt=row[1], iteration_count=row[2], stored_key=row[3], server_key=row[4]
        )

    def list_usernames(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT username FROM scram_verifiers ORDER BY username")]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
