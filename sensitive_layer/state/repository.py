"""Record and blind-index persistence. No crypto happens here."""
from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, db) -> None:
        self._db = db

    # --- records ---
    def upsert_record(self, record_id, field, purpose, ciphertext, enc_key_version):
        with self._db.lock:
            self._db.conn.execute(
                """INSERT INTO records(record_id, field, purpose, ciphertext,
                                       enc_key_version, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(record_id, field, purpose)
                   DO UPDATE SET ciphertext=excluded.ciphertext,
                                 enc_key_version=excluded.enc_key_version,
                                 updated_at=excluded.updated_at""",
                (record_id, field, purpose, ciphertext, enc_key_version, _now()),
            )
            self._db.conn.commit()

    def get_record(self, record_id, field, purpose):
        with self._db.lock:
            return self._db.conn.execute(
                "SELECT * FROM records WHERE record_id=? AND field=? AND purpose=?",
                (record_id, field, purpose),
            ).fetchone()

    def iter_records(self):
        with self._db.lock:
            return self._db.conn.execute("SELECT * FROM records").fetchall()

    # --- blind indexes ---
    def put_index(self, record_id, field, purpose, version, index_value):
        with self._db.lock:
            self._db.conn.execute(
                """INSERT INTO blind_indexes(record_id, field, purpose,
                                             index_key_version, index_value)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(record_id, field, purpose, index_key_version)
                   DO UPDATE SET index_value=excluded.index_value""",
                (record_id, field, purpose, version, index_value),
            )
            self._db.conn.commit()

    def delete_indexes(self, record_id, field, purpose):
        with self._db.lock:
            self._db.conn.execute(
                "DELETE FROM blind_indexes"
                " WHERE record_id=? AND field=? AND purpose=?",
                (record_id, field, purpose),
            )
            self._db.conn.commit()

    def delete_index_version(self, version) -> int:
        with self._db.lock:
            cur = self._db.conn.execute(
                "DELETE FROM blind_indexes WHERE index_key_version=?", (version,))
            self._db.conn.commit()
            return cur.rowcount

    def index_rows(self, record_id, field, purpose):
        with self._db.lock:
            return self._db.conn.execute(
                "SELECT index_key_version, index_value FROM blind_indexes"
                " WHERE record_id=? AND field=? AND purpose=?"
                " ORDER BY index_key_version",
                (record_id, field, purpose),
            ).fetchall()

    def find_candidates(self, field, purpose, versioned_indexes):
        """versioned_indexes: list of (version, index_bytes).

        Returns distinct record_ids whose stored index matches under ANY of the
        given key versions (dual-version lookup during key rotation).
        """
        if not versioned_indexes:
            return []
        clause = " OR ".join(
            "(index_key_version=? AND index_value=?)" for _ in versioned_indexes)
        args: list = [field, purpose]
        for version, index_value in versioned_indexes:
            args += [version, index_value]
        with self._db.lock:
            rows = self._db.conn.execute(
                f"SELECT DISTINCT record_id FROM blind_indexes"
                f" WHERE field=? AND purpose=? AND ({clause})",
                args,
            ).fetchall()
        return [r[0] for r in rows]

    # --- meta ---
    def get_meta(self, key, default=None):
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO meta(k, v) VALUES (?,?)"
                " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, value),
            )
            self._db.conn.commit()
