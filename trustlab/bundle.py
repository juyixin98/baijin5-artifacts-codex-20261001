"""Versioned trust-bundle management: rotation, rollback, overlap rules.

Core invariant: bundle version numbers are strictly monotonic. A rollback
emits a NEW version carrying older content; historical versions are never
reused or mutated, so an audit trail can always tell "which roots were
trusted at time T" without ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from cryptography import x509

from .certs import fingerprint_sha256, load_pem_certificate
from .errors import InputError, StateConflict
from .store import Store


@dataclass
class Bundle:
    version: int
    roots_pem: list[str]
    status: str
    kind: str
    note: str

    @property
    def root_fingerprints(self) -> list[str]:
        return sorted(
            fingerprint_sha256(load_pem_certificate(p)) for p in self.roots_pem
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "status": self.status,
            "kind": self.kind,
            "note": self.note,
            "root_fingerprints": self.root_fingerprints,
            "root_count": len(self.roots_pem),
        }


class BundleManager:
    def __init__(self, store: Store, *, run_id: str,
                 on_activated: Callable[[Bundle], None] | None = None) -> None:
        self._store = store
        self._run_id = run_id
        self._on_activated = on_activated

    # -- creation -----------------------------------------------------------
    def create(self, roots_pem: list[str], *, kind: str = "manual",
               note: str = "",
               explicit_version: int | None = None) -> Bundle:
        roots = self._validate_roots(roots_pem)
        version = self._next_version(explicit_version)
        previous = self._store.active_bundle()
        self._store.insert_bundle(
            version=version, roots=roots, status="active", kind=kind, note=note
        )
        if previous is not None:
            self._store.set_bundle_status(previous["version"], "retired")
        bundle = Bundle(version, roots, "active", kind, note)
        self._store.audit(
            run_id=self._run_id,
            category="STATE_CHANGE",
            event="bundle_activated",
            detail={
                "version": version,
                "kind": kind,
                "root_fingerprints": bundle.root_fingerprints,
                "previous_version": previous["version"] if previous else None,
                "retired_root_fingerprints": self._retired_fps(previous, bundle),
            },
            reasoning=(
                f"bundle v{version} activated as the only active bundle"
                + (f"; v{previous['version']} retired" if previous else "")
            ),
        )
        if self._on_activated is not None:
            self._on_activated(bundle)
        return bundle

    def begin_rotation(self, new_roots_pem: list[str], *,
                       note: str = "") -> Bundle:
        """Overlap phase: trust old AND new roots simultaneously."""
        active = self._require_active()
        union = self._validate_roots(active["roots"] + list(new_roots_pem))
        return self.create(
            union,
            kind="rotation_overlap",
            note=note or "overlap: old and new roots trusted simultaneously",
        )

    def end_rotation(self, new_roots_pem: list[str], *,
                     note: str = "") -> Bundle:
        """Final phase: only the new roots remain trusted."""
        return self.create(
            list(new_roots_pem),
            kind="rotation_final",
            note=note or "rotation finalized: only new roots trusted",
        )

    def rollback(self, to_version: int, *, note: str = "") -> Bundle:
        target = self._store.get_bundle(to_version)
        if target is None:
            raise InputError(
                f"cannot roll back: bundle v{to_version} does not exist",
                detail={"to_version": to_version},
                reasoning="rollback target must be an existing historical "
                          "bundle version",
            )
        new_version = self._store.max_bundle_version() + 1
        return self.create(
            target["roots"],
            kind="rollback",
            note=note or (
                f"rollback to content of v{to_version}, emitted as new "
                f"version v{new_version}; version numbers are never reused"
            ),
        )

    # -- queries --------------------------------------------------------------
    def active(self) -> Bundle | None:
        row = self._store.active_bundle()
        return self._from_row(row) if row else None

    def history(self) -> list[Bundle]:
        return [self._from_row(r) for r in self._store.list_bundles()]

    # -- internals --------------------------------------------------------------
    def _next_version(self, explicit_version: int | None) -> int:
        expected = self._store.max_bundle_version() + 1
        if explicit_version is not None and explicit_version != expected:
            raise StateConflict(
                f"bundle version {explicit_version} rejected: next version "
                f"must be {expected}",
                detail={"requested": explicit_version, "expected": expected},
                reasoning="version numbers are monotonic and never reused; "
                          "a rollback emits a new version with old content "
                          "instead of reusing the old number",
            )
        return expected

    def _require_active(self) -> dict:
        active = self._store.active_bundle()
        if active is None:
            raise StateConflict("no active trust bundle exists")
        return active

    @staticmethod
    def _validate_roots(roots_pem: list[str]) -> list[str]:
        if not roots_pem:
            raise InputError("trust bundle must contain at least one root")
        seen: dict[str, str] = {}
        for pem in roots_pem:
            try:
                cert = load_pem_certificate(pem)
            except Exception as exc:
                raise InputError(
                    f"unparsable root certificate PEM: {exc}",
                    reasoning="bundle roots must be valid PEM-encoded "
                              "X.509 certificates",
                ) from exc
            try:
                bc = cert.extensions.get_extension_for_class(
                    x509.BasicConstraints
                ).value
                is_ca = bc.ca
            except x509.ExtensionNotFound:
                is_ca = False
            if not is_ca:
                raise InputError(
                    "bundle root is not a CA certificate "
                    "(basicConstraints CA:TRUE required)",
                    reasoning="trust anchors must be CA certificates",
                )
            seen[fingerprint_sha256(cert)] = pem
        return list(seen.values())

    @staticmethod
    def _retired_fps(previous: dict | None, current: Bundle) -> list[str]:
        if previous is None:
            return []
        old = {
            fingerprint_sha256(load_pem_certificate(p))
            for p in previous["roots"]
        }
        new = set(current.root_fingerprints)
        return sorted(old - new)

    @staticmethod
    def _from_row(row: dict) -> Bundle:
        return Bundle(row["version"], list(row["roots"]), row["status"],
                      row["kind"], row["note"])
