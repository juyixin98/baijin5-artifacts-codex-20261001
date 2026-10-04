#!/usr/bin/env python3
"""Provision a LOCAL TEST account: derive and store a salted SCRAM verifier.

Usage:
    python scripts/provision_account.py --username alice
    python scripts/provision_account.py -u bob -i 8192

The password is read from the SCRAM_TEST_PASSWORD environment variable or
prompted without echo. The plaintext password never touches disk: only
StoredKey/ServerKey, salt and iteration count are persisted in SQLite.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scram_auth.config import load_config
from scram_auth.saslprep import SaslprepError
from scram_auth.verifiers import VerifierRepository, build_verifier


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision a local SCRAM-SHA-256 test account.")
    parser.add_argument("-u", "--username", required=True, help="local test username (SASLprep applied)")
    parser.add_argument("-i", "--iterations", type=int, default=None, help="iteration count (default: config)")
    parser.add_argument("-c", "--config", default=os.environ.get("SCRAM_CONFIG", "config/config.toml"))
    parser.add_argument("--show-redacted", action="store_true", help="print the redacted verifier structure")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    password = os.environ.get("SCRAM_TEST_PASSWORD") or getpass.getpass("local test password (not stored): ")
    if not password:
        print("error: empty password", file=sys.stderr)
        return 2
    iterations = args.iterations or config.crypto.iteration_count
    try:
        verifier = build_verifier(
            password,
            iterations=iterations,
            salt_bytes=config.crypto.salt_bytes,
        )
    except SaslprepError as exc:
        print(f"error: SASLprep rejected the password: {exc}", file=sys.stderr)
        return 2

    Path(config.server.database_path).parent.mkdir(parents=True, exist_ok=True)
    repo = VerifierRepository(config.server.database_path)
    repo.upsert(args.username, verifier)
    names = repo.list_usernames()
    if args.show_redacted:
        stored = repo.get(args.username)
        assert stored is not None
        print(stored.redacted())
    repo.close()
    print(f"provisioned local test account {args.username!r}; accounts now: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
