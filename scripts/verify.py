#!/usr/bin/env python3
"""Standalone verification script for the keytree service.

Runs the acceptance checks end-to-end against a throwaway database and
log file, prints a per-check PASS/FAIL report keyed by a verification
run id, and exits non-zero if any check fails.

Usage: .venv/bin/python scripts/verify.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keytree.crypto_adapter import (  # noqa: E402
    MAX_OKM_LEN,
    CryptographyHkdf,
    PyCryptodomeHkdf,
)
from keytree.encoding import encode_info  # noqa: E402
from keytree.errors import (  # noqa: E402
    InputValidationError,
    ResourceExhaustedError,
    StateConflictError,
)
from keytree.identity import KeyIdentity  # noqa: E402
from keytree.logging_config import new_run_id  # noqa: E402
from keytree.service import KeyTreeService  # noqa: E402
from keytree.store import Store  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
ROOT_HEX = (FIXTURES / "test_root.hex").read_text().strip()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def decorator(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception as exc:  # report, don't stop: run every check
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return decorator


@check("rfc5869 golden vectors (cryptography)")
def _():
    cases = json.loads((FIXTURES / "rfc5869.json").read_text())["cases"]
    backend = CryptographyHkdf()
    for c in cases:
        okm = backend.hkdf_sha256(bytes.fromhex(c["ikm"]), bytes.fromhex(c["salt"]),
                                  bytes.fromhex(c["info"]), c["length"])
        assert okm.hex() == c["okm"], c["name"]


@check("rfc5869 golden vectors (pycryptodome)")
def _():
    cases = json.loads((FIXTURES / "rfc5869.json").read_text())["cases"]
    backend = PyCryptodomeHkdf()
    for c in cases:
        okm = backend.hkdf_sha256(bytes.fromhex(c["ikm"]), bytes.fromhex(c["salt"]),
                                  bytes.fromhex(c["info"]), c["length"])
        assert okm.hex() == c["okm"], c["name"]


@check("label concatenation collision counterexample")
def _():
    assert (b"ab" + b"c") == (b"a" + b"bc")  # naive concat collides
    assert encode_info([(b"x", b"ab"), (b"y", b"c")]) != \
           encode_info([(b"x", b"a"), (b"y", b"bc")])


@check("golden tree vectors (independent PyCryptodome reference)")
def _():
    vectors = json.loads((FIXTURES / "golden_tree_vectors.json").read_text())["vectors"]
    with tempfile.TemporaryDirectory() as d:
        svc = KeyTreeService(Store(Path(d) / "s.db"), root_hex=ROOT_HEX)
        for v in vectors:
            ident = KeyIdentity(v["tenant"], v["purpose"], v["version"],
                                bytes.fromhex(v["context_hex"]))
            r = svc.derive(ident, length=v["length"])
            assert r.key_bytes.hex() == v["key_hex"], v["key_id"]
            assert r.key_id == v["key_id"]
            assert r.fingerprint == v["fingerprint"]


@check("same request is deterministic")
def _():
    with tempfile.TemporaryDirectory() as d:
        svc = KeyTreeService(Store(Path(d) / "s.db"), root_hex=ROOT_HEX)
        ident = KeyIdentity("acme", "encryption", 1, b"")
        assert svc.derive(ident).key_bytes == svc.derive(ident).key_bytes


@check("different purposes are separated")
def _():
    with tempfile.TemporaryDirectory() as d:
        svc = KeyTreeService(Store(Path(d) / "s.db"), root_hex=ROOT_HEX)
        a = svc.derive(KeyIdentity("acme", "encryption", 1, b""))
        b = svc.derive(KeyIdentity("acme", "signing", 1, b""))
        assert a.key_bytes != b.key_bytes and a.key_id != b.key_id


@check("input error category (invalid tenant)")
def _():
    try:
        KeyIdentity("BAD TENANT", "p", 0, b"")
    except InputValidationError as exc:
        assert exc.category == "input_error"
    else:
        raise AssertionError("expected InputValidationError")


@check("resource exhaustion category (length > HKDF max)")
def _():
    with tempfile.TemporaryDirectory() as d:
        svc = KeyTreeService(Store(Path(d) / "s.db"), root_hex=ROOT_HEX)
        try:
            svc.derive(KeyIdentity("acme", "p", 0, b""), length=MAX_OKM_LEN + 1)
        except ResourceExhaustedError as exc:
            assert exc.category == "resource_exhausted"
        else:
            raise AssertionError("expected ResourceExhaustedError")


@check("state conflict category (tampered registry)")
def _():
    with tempfile.TemporaryDirectory() as d:
        store = Store(Path(d) / "s.db")
        svc = KeyTreeService(store, root_hex=ROOT_HEX)
        ident = KeyIdentity("acme", "p", 0, b"")
        r = svc.derive(ident)
        with store._lock:
            store._conn.execute("UPDATE registry SET fingerprint = ? WHERE key_id = ?",
                                ("00" * 16, r.key_id))
            store._conn.commit()
        try:
            svc.derive(ident)
        except StateConflictError as exc:
            assert exc.category == "state_conflict"
        else:
            raise AssertionError("expected StateConflictError")


@check("key material absent from diagnostic log")
def _():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "run.log"
        stream = open(log_path, "a", encoding="utf-8")
        svc = KeyTreeService(Store(Path(d) / "s.db"), log_stream=stream,
                             root_hex=ROOT_HEX)
        r = svc.derive(KeyIdentity("acme", "encryption", 1, b""))
        stream.close()
        content = log_path.read_text()
        assert ROOT_HEX not in content
        assert r.key_bytes.hex() not in content
        assert r.key_id in content  # safe identifiers are logged


def main() -> int:
    run_id = new_run_id()
    print(f"verification run_id: {run_id}")
    width = max(len(name) for name, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name.ljust(width)} {detail if not ok else ''}")
        failed += 0 if ok else 1
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
