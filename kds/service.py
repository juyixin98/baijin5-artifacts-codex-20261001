"""The derivation tree service.

Tree layout (each edge is one HKDF-SHA256 expansion with the fixed protocol
salt and an unambiguous, level-tagged ``info`` produced by kds.encoding)::

    root (local test fixture)
      └─ tenant      k1 = HKDF(root, info=enc("kds1","level","tenant",tenant))
            └─ purpose   k2 = HKDF(k1, info=enc("kds1","level","purpose",purpose))
                  └─ version   k3 = HKDF(k2, info=enc("kds1","level","version",version))
                        └─ context   k4 = HKDF(k3, info=enc("kds1","level","context",context))
                              └─ output    out = HKDF(k4, info=enc("kds1","output",key_id), L)

Determinism: identical (root, identity, length) always yields identical
output. Separation: any difference in tenant/purpose/version/context changes
at least one ``info`` string, hence the output.

Every operation is audited with its run id, a digest of the request, the
outcome and — on failure — the machine-readable error category. Key
material is never logged or persisted.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from .crypto_adapter import MAX_DERIVE_LEN, hkdf_sha256
from .encoding import encode_fields
from .errors import ComputationError, KdsError
from .identity import KeyIdentity
from .logging_config import get_logger, log_with_run
from .state import StateStore, new_run_id

TREE_KEY_LEN = 32
ROOT_KEY_ENV = "KDS_ROOT_KEY_HEX"


@dataclass(frozen=True)
class DerivedKey:
    key_id: str
    key: bytes
    length: int
    run_id: str


class DerivationTreeService:
    def __init__(
        self,
        root_key: bytes,
        store: StateStore,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        if len(root_key) < 16:
            raise KdsError("root key must be at least 16 bytes")
        self._root = bytes(root_key)
        self._store = store
        self._log = logger or get_logger()

    @classmethod
    def from_env(cls, store: StateStore, **kwargs) -> "DerivationTreeService":
        hex_key = os.environ.get(ROOT_KEY_ENV)
        if hex_key is None:
            raise KdsError(
                f"root key not provided; set {ROOT_KEY_ENV} to a hex-encoded test key"
            )
        return cls(bytes.fromhex(hex_key), store, **kwargs)

    # -- public operations --------------------------------------------------

    def register(
        self, identity: KeyIdentity, display_name: str, *, run_id: str | None = None
    ) -> str:
        run_id = run_id or new_run_id()
        try:
            self._store.register_key(identity, display_name, run_id=run_id)
        except KdsError as exc:
            self._audit_and_log(
                run_id, "register", identity, "error", reason=exc.category
            )
            raise
        self._audit_and_log(run_id, "register", identity, "ok")
        return identity.key_id

    def derive(
        self,
        identity: KeyIdentity,
        *,
        length: int = TREE_KEY_LEN,
        run_id: str | None = None,
    ) -> DerivedKey:
        run_id = run_id or new_run_id()
        try:
            key = self._derive_tree(identity, length)
        except KdsError as exc:
            self._audit_and_log(run_id, "derive", identity, "error", reason=exc.category)
            raise
        except Exception as exc:  # backend failure -> computation category
            wrapped = ComputationError(
                "cryptographic backend failed", detail=type(exc).__name__
            )
            self._audit_and_log(
                run_id, "derive", identity, "error", reason=wrapped.category
            )
            raise wrapped from exc
        self._audit_and_log(run_id, "derive", identity, "ok")
        return DerivedKey(
            key_id=identity.key_id, key=key, length=length, run_id=run_id
        )

    # -- internals ------------------------------------------------------------

    def _derive_tree(self, identity: KeyIdentity, length: int) -> bytes:
        node = self._root
        for level, value in (
            ("tenant", identity.tenant),
            ("purpose", identity.purpose),
            ("version", identity.version),
            ("context", identity.context),
        ):
            info = encode_fields("kds1", "level", level, value)
            node = hkdf_sha256(node, info, TREE_KEY_LEN)
        out_info = encode_fields("kds1", "output", identity.key_id)
        return hkdf_sha256(node, out_info, length)

    def _request_hash(self, identity: KeyIdentity) -> str:
        return hashlib.sha256(identity.canonical()).hexdigest()[:16]

    def _audit_and_log(
        self,
        run_id: str,
        event: str,
        identity: KeyIdentity,
        outcome: str,
        *,
        reason: str | None = None,
    ) -> None:
        request_hash = self._request_hash(identity)
        self._store.record_audit(
            run_id=run_id,
            event=event,
            key_id=identity.key_id,
            request_hash=request_hash,
            outcome=outcome,
            reason=reason,
        )
        log_with_run(
            self._log,
            logging.INFO if outcome == "ok" else logging.WARNING,
            run_id,
            "event=%s key_id=%s request=%s outcome=%s reason=%s",
            event,
            identity.key_id,
            request_hash,
            outcome,
            reason or "-",
        )


__all__ = [
    "DerivationTreeService",
    "DerivedKey",
    "MAX_DERIVE_LEN",
    "ROOT_KEY_ENV",
    "TREE_KEY_LEN",
]
