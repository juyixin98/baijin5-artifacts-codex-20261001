"""Derivation tree service.

Tree shape (all levels HKDF-SHA256, fixed roles):

    salt = FIXED_SALT for every level (deployment constant, never caller input)
    info = TLV-encoded level label (see encoding.py)

    root  (stored locally, 32 bytes)
      └─ L1 tenant   info = KDT1[("scope","tenant"),  ("tenant",  tenant)]
         └─ L2 purpose info = KDT1[("scope","purpose"), ("purpose", purpose)]
            └─ L3 version  info = KDT1[("scope","version"), ("version", u32be)]
               └─ L4 context info = KDT1[("scope","context"), ("context", ctx)]
                  → final key (caller-chosen length, bounded by HKDF max)

Intermediate levels always derive 32 bytes. Determinism: identical identity
+ length always yields identical key material. Separation: distinct
identities yield distinct info blocks (TLV is injective), hence distinct
keys with overwhelming probability.

The service logs intermediate states per run (validated, root loaded, each
level derived, registered, completed/failed) with rationale, but never logs
key material — only key_id and the HMAC fingerprint.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass
from typing import TextIO

from . import encoding
from .crypto_adapter import CryptographyHkdf, HkdfBackend
from .errors import KeyTreeError
from .identity import KeyIdentity
from .logging_config import RunLogger, new_run_id
from .store import Store

#: Fixed deployment salt for the local test deployment. Its role is fixed:
#: it is always the HKDF salt, at every level, and is never caller-controlled.
FIXED_SALT = hashlib.sha256(b"keytree-local-test-salt-v1").digest()

#: Domain separation tag for the key-verification fingerprint.
_FINGERPRINT_TAG = b"keytree-check-v1"

_INTERMEDIATE_LEN = 32
_ROOT_META_KEY = "root_key_hex"


@dataclass(frozen=True)
class DerivedKey:
    """Result of a derivation. `key_bytes` is the only secret field and is
    never written to logs, audit, or the registry."""

    key_id: str
    key_bytes: bytes
    fingerprint: str
    length: int
    run_id: str


def fingerprint_of(key_bytes: bytes) -> str:
    """Verification tag for a derived key; safe to store and log."""
    return hmac.new(key_bytes, _FINGERPRINT_TAG, hashlib.sha256).hexdigest()[:32]


class KeyTreeService:
    def __init__(
        self,
        store: Store,
        *,
        backend: HkdfBackend | None = None,
        log_stream: TextIO | None = None,
        root_hex: str | None = None,
    ):
        self._store = store
        self._backend = backend or CryptographyHkdf()
        self._log_stream = log_stream
        if root_hex is not None:
            root = bytes.fromhex(root_hex)
            if len(root) != _INTERMEDIATE_LEN:
                raise KeyTreeError(
                    "injected root must be 32 bytes",
                    details={"length": len(root)},
                )
            self._store.set_meta_if_absent(_ROOT_META_KEY, root.hex())

    # -- internals ----------------------------------------------------------

    def _logger(self, run_id: str) -> RunLogger | None:
        if self._log_stream is None:
            return None
        return RunLogger(self._log_stream, run_id)

    @staticmethod
    def _log(logger: RunLogger | None, event: str, **fields) -> None:
        if logger is not None:
            logger.event(event, **fields)

    def _load_root(self) -> bytes:
        hexval = self._store.get_meta(_ROOT_META_KEY)
        if hexval is None:
            root = secrets.token_bytes(_INTERMEDIATE_LEN)
            self._store.set_meta_if_absent(_ROOT_META_KEY, root.hex())
            hexval = self._store.get_meta(_ROOT_META_KEY)
            assert hexval is not None  # set_meta_if_absent just ran
        return bytes.fromhex(hexval)

    def _level_info(self, scope: bytes, tag: bytes, value: bytes) -> bytes:
        return encoding.encode_info([(b"scope", scope), (tag, value)])

    def derive_chain(self, identity: KeyIdentity, length: int) -> tuple[bytes, list[str]]:
        """Walk the tree; returns (final_key, level_names) for logging."""
        chain = self._load_root()
        levels: list[tuple[bytes, bytes, bytes, int]] = [
            (b"tenant", b"tenant", identity.tenant.encode("utf-8"), _INTERMEDIATE_LEN),
            (b"purpose", b"purpose", identity.purpose.encode("utf-8"), _INTERMEDIATE_LEN),
            (b"version", b"version", struct.pack(">I", identity.version), _INTERMEDIATE_LEN),
            (b"context", b"context", identity.context, length),
        ]
        names: list[str] = []
        for scope, tag, value, out_len in levels:
            info = self._level_info(scope, tag, value)
            chain = self._backend.hkdf_sha256(chain, FIXED_SALT, info, out_len)
            names.append(scope.decode("ascii"))
        return chain, names

    # -- public API -----------------------------------------------------------

    def derive(
        self,
        identity: KeyIdentity,
        *,
        length: int = 32,
        display_name: str | None = None,
        run_id: str | None = None,
    ) -> DerivedKey:
        run_id = run_id or new_run_id()
        logger = self._logger(run_id)
        key_id = identity.key_id
        try:
            self._log(
                logger,
                "input_validated",
                key_id=key_id,
                length=length,
                rationale="identity tuple passed schema validation",
            )
            key_bytes, levels = self.derive_chain(identity, length)
            for idx, level in enumerate(levels):
                self._log(
                    logger,
                    "level_derived",
                    key_id=key_id,
                    level_index=idx,
                    level=level,
                    rationale="HKDF-SHA256 with fixed salt and TLV-encoded label",
                )
            fpr = fingerprint_of(key_bytes)
            self._store.register_key(
                key_id=key_id,
                descriptor_hex=identity.descriptor().hex(),
                display_name=display_name,
                fingerprint=fpr,
                length=length,
                run_id=run_id,
            )
            if display_name is not None:
                self._store.bind_name(display_name, key_id)
            self._store.audit(
                run_id=run_id,
                event="derive",
                outcome="success",
                key_id=key_id,
                detail={"length": length, "fingerprint": fpr},
            )
            self._log(
                logger,
                "completed",
                key_id=key_id,
                fingerprint=fpr,
                rationale="key derived, fingerprinted, and registered",
            )
            return DerivedKey(
                key_id=key_id,
                key_bytes=key_bytes,
                fingerprint=fpr,
                length=length,
                run_id=run_id,
            )
        except KeyTreeError as exc:
            self._store.audit(
                run_id=run_id,
                event="derive",
                outcome="failure",
                key_id=key_id,
                error_category=exc.category,
                detail={"message": exc.message},
            )
            self._log(
                logger,
                "failed",
                key_id=key_id,
                error_category=exc.category,
                rationale=exc.message,
            )
            raise
