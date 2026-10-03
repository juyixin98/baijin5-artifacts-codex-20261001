#!/usr/bin/env python3
"""Regenerate the local synthetic fixtures deterministically.

Everything here is synthetic test data — no production accounts, no real
business data. The root key is derived from a fixed public seed string so
the fixture is reproducible; it is a *test* root and must never be used
outside local testing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

ROOT_SEED = b"kds local synthetic test root seed v1 (not for production)"

TENANTS = [
    {"tenant": "tenant-alpha", "display_name": "Alpha (synthetic)"},
    {"tenant": "tenant-beta", "display_name": "Beta (synthetic)"},
    # Two tenants intentionally sharing a display name: identity must not be
    # confusable via display names.
    {"tenant": "tenant-gamma", "display_name": "Alpha (synthetic)"},
]

PURPOSES = ["encryption", "signing", "backup"]


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    root = hashlib.sha256(ROOT_SEED).digest()
    (FIXTURES / "root_key.hex").write_text(root.hex() + "\n")
    (FIXTURES / "tenants.json").write_text(
        json.dumps({"tenants": TENANTS, "purposes": PURPOSES}, indent=2) + "\n"
    )
    print(f"wrote {FIXTURES / 'root_key.hex'} and {FIXTURES / 'tenants.json'}")


if __name__ == "__main__":
    main()
