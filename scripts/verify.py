#!/usr/bin/env python3
"""Standalone verification script (independent of pytest).

Runs the acceptance checks end to end and prints one PASS/FAIL line per
check with a run id, so a failure can be replayed. Exit code is non-zero if
any check fails.

Checks:
  1. RFC 5869 golden vectors against BOTH HKDF backends.
  2. Label-encoding collision counterexample ("ab","c") vs ("a","bc").
  3. Service determinism (same request -> same key).
  4. Purpose separation (different purpose -> different key).
  5. Service output equals an independent recomputation via the reference
     backend (expected value not produced by the primary code path).
  6. Error taxonomy: bad label -> input, oversized length ->
     resource_exhausted, display-name clash -> state_conflict.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kds.crypto_adapter import MAX_DERIVE_LEN, hkdf_sha256, hkdf_sha256_reference
from kds.encoding import encode_fields
from kds.errors import InputError, ResourceExhaustedError, StateConflictError
from kds.identity import KeyIdentity
from kds.service import TREE_KEY_LEN, DerivationTreeService
from kds.state import StateStore, new_run_id

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

RFC5869_CASES = [
    {
        "name": "rfc5869-case1",
        "ikm": bytes.fromhex("0b" * 22),
        "salt": bytes.fromhex("000102030405060708090a0b0c"),
        "info": bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        "length": 42,
        "okm": (
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        ),
    },
    {
        "name": "rfc5869-case2",
        "ikm": bytes(range(0x00, 0x50)),
        "salt": bytes(range(0x60, 0xB0)),
        "info": bytes(range(0xB0, 0x100)),
        "length": 82,
        "okm": (
            "b11e398dc80327a1c8e7f78c596a4934"
            "4f012eda2d4efad8a050cc4c19afa97c"
            "59045a99cac7827271cb41c65e590e09"
            "da3275600c2f09b8367793a9aca3db71"
            "cc30c58179ec3e87c14c01d5c1f3434f"
            "1d87"
        ),
    },
    {
        "name": "rfc5869-case3",
        "ikm": bytes.fromhex("0b" * 22),
        "salt": b"",
        "info": b"",
        "length": 42,
        "okm": (
            "8da4e775a563c18f715f802a063c5a31"
            "b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8"
        ),
    },
]

IDENT = KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=1, context="nightly")


def main() -> int:
    run_id = new_run_id()
    print(f"verification run_id={run_id}")
    failures: list[str] = []

    def check(name: str, fn) -> None:
        try:
            fn()
        except Exception:
            failures.append(name)
            print(f"FAIL {name} (run_id={run_id})")
            traceback.print_exc()
        else:
            print(f"PASS {name}")

    def golden_vectors() -> None:
        for case in RFC5869_CASES:
            for backend in (hkdf_sha256, hkdf_sha256_reference):
                got = backend(
                    case["ikm"], case["info"], case["length"], salt=case["salt"]
                ).hex()
                assert got == case["okm"], f"{case['name']}: {got} != {case['okm']}"

    def encoding_counterexample() -> None:
        assert encode_fields("ab", "c") != encode_fields("a", "bc")

    root = bytes.fromhex((FIXTURES / "root_key.hex").read_text().strip())
    tmp = tempfile.TemporaryDirectory()
    store = StateStore(str(Path(tmp.name) / "verify.sqlite3"))
    service = DerivationTreeService(root, store)

    def determinism() -> None:
        assert service.derive(IDENT).key == service.derive(IDENT).key

    def separation() -> None:
        other = KeyIdentity(
            tenant="tenant-alpha", purpose="signing", version=1, context="nightly"
        )
        assert service.derive(IDENT).key != service.derive(other).key

    def independent_recompute() -> None:
        node = root
        for level, value in (
            ("tenant", IDENT.tenant),
            ("purpose", IDENT.purpose),
            ("version", IDENT.version),
            ("context", IDENT.context),
        ):
            node = hkdf_sha256_reference(
                node, encode_fields("kds1", "level", level, value), TREE_KEY_LEN
            )
        expected = hkdf_sha256_reference(
            node, encode_fields("kds1", "output", IDENT.key_id), 32
        )
        assert service.derive(IDENT).key == expected

    def error_taxonomy() -> None:
        try:
            KeyIdentity(tenant="Bad Tenant", purpose="p", version=1, context="c")
        except InputError:
            pass
        else:
            raise AssertionError("bad label did not raise InputError")
        try:
            service.derive(IDENT, length=MAX_DERIVE_LEN + 1)
        except ResourceExhaustedError:
            pass
        else:
            raise AssertionError("oversized length did not raise ResourceExhaustedError")
        service.register(IDENT, "nightly key")
        try:
            service.register(IDENT, "renamed key")
        except StateConflictError:
            pass
        else:
            raise AssertionError("display-name clash did not raise StateConflictError")

    check("golden-vectors", golden_vectors)
    check("encoding-collision-counterexample", encoding_counterexample)
    check("determinism", determinism)
    check("purpose-separation", separation)
    check("independent-recompute", independent_recompute)
    check("error-taxonomy", error_taxonomy)

    store.close()
    tmp.cleanup()
    if failures:
        print(f"run_id={run_id}: {len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print(f"run_id={run_id}: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
