"""One-off fixture generator.

Derives verification/fixtures.json from plaintext inputs using ONLY the
stdlib reference (verification/reference.py) — never the service under test.
Re-run after changing config/settings.verify.json keys:

    .venv/bin/python verification/gen_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verification import reference  # noqa: E402

VERIFY_CONFIG = json.loads(
    (Path(__file__).resolve().parent.parent / "config/settings.verify.json")
    .read_text())
INDEX_KEY_V1 = VERIFY_CONFIG["keys"]["index"]["1"]
INDEX_BITS = VERIFY_CONFIG["index_bits"]
NORM_VERSION = VERIFY_CONFIG["norm_version"]

EMAIL = "lookup:email"
ALIAS = "alias:email"
PHONE = "lookup:phone"
NAME = "lookup:name"


def find_collision_pair() -> tuple[str, str]:
    """Two distinct emails whose 16-bit blind indexes collide (birthday search)."""
    seen: dict[str, str] = {}
    n = 0
    while True:
        value = f"collide{n}@example.com"
        idx = reference.blind_index_hex(
            INDEX_KEY_V1, EMAIL, NORM_VERSION,
            reference.normalize(value, "email"), INDEX_BITS)
        if idx in seen and seen[idx] != value:
            return seen[idx], value
        seen[idx] = value
        n += 1


def main() -> None:
    coll_a, coll_b = find_collision_pair()

    records = [
        # Same canonical value, three different surface representations.
        {"record_id": "r1", "field": "email", "purpose": EMAIL,
         "value": "  Alice@Example.COM "},
        {"record_id": "r2", "field": "email", "purpose": EMAIL,
         "value": "alice@example.com"},
        {"record_id": "r3", "field": "email", "purpose": EMAIL,
         "value": "ａｌｉｃｅ＠ｅｘａｍｐｌｅ．ｃｏｍ"},  # full-width
        {"record_id": "r4", "field": "email", "purpose": EMAIL,
         "value": "bob@example.com"},
        {"record_id": "r5", "field": "email", "purpose": EMAIL, "value": None},
        {"record_id": "r6", "field": "phone", "purpose": PHONE,
         "value": "1 (555) 010-1234"},
        {"record_id": "r7", "field": "phone", "purpose": PHONE,
         "value": "15550101234"},
        # Forced collision pair at the configured short index length.
        {"record_id": "r8", "field": "email", "purpose": EMAIL, "value": coll_a},
        {"record_id": "r9", "field": "email", "purpose": EMAIL, "value": coll_b},
        # Same value stored under a DIFFERENT purpose: must not cross-match.
        {"record_id": "r10", "field": "email", "purpose": ALIAS,
         "value": "dave@example.com"},
        {"record_id": "r11", "field": "name", "purpose": NAME,
         "value": "  Alice   Smith "},
    ]

    field_types = {EMAIL: "email", ALIAS: "email", PHONE: "phone", NAME: "name"}

    def expect(field, purpose, value):
        return reference.expected_matches(
            records, field, purpose, field_types[purpose], value)

    queries = [
        {"name": "canonical-case-pad", "field": "email", "purpose": EMAIL,
         "value": "ALICE@example.com",
         "expected_confirmed": expect("email", EMAIL, "ALICE@example.com")},
        {"name": "fullwidth-form", "field": "email", "purpose": EMAIL,
         "value": "Ａｌｉｃｅ＠Ｅｘａｍｐｌｅ．Ｃｏｍ",
         "expected_confirmed": expect("email", EMAIL, "Ａｌｉｃｅ＠Ｅｘａｍｐｌｅ．Ｃｏｍ")},
        {"name": "plain-bob", "field": "email", "purpose": EMAIL,
         "value": "bob@example.com",
         "expected_confirmed": expect("email", EMAIL, "bob@example.com")},
        {"name": "absent", "field": "email", "purpose": EMAIL,
         "value": "carol@example.com",
         "expected_confirmed": expect("email", EMAIL, "carol@example.com")},
        {"name": "phone-punctuation", "field": "phone", "purpose": PHONE,
         "value": "1-555-010-1234",
         "expected_confirmed": expect("phone", PHONE, "1-555-010-1234")},
        {"name": "forced-collision-a", "field": "email", "purpose": EMAIL,
         "value": coll_a, "expected_confirmed": expect("email", EMAIL, coll_a),
         "expect_filtered": True},
        {"name": "forced-collision-b", "field": "email", "purpose": EMAIL,
         "value": coll_b, "expected_confirmed": expect("email", EMAIL, coll_b),
         "expect_filtered": True},
        {"name": "domain-separation", "field": "email", "purpose": EMAIL,
         "value": "dave@example.com",
         "expected_confirmed": expect("email", EMAIL, "dave@example.com")},
        {"name": "name-whitespace-case", "field": "name", "purpose": NAME,
         "value": "alice smith",
         "expected_confirmed": expect("name", NAME, "alice smith")},
    ]

    fixtures = {
        "norm_version": NORM_VERSION,
        "index_bits": INDEX_BITS,
        "index_key_v1": INDEX_KEY_V1,
        "field_types": field_types,
        "records": records,
        "queries": queries,
        "index_probes": [
            {"record_id": "r1", "field": "email", "purpose": EMAIL,
             "value": "  Alice@Example.COM "},
            {"record_id": "r4", "field": "email", "purpose": EMAIL,
             "value": "bob@example.com"},
            {"record_id": "r6", "field": "phone", "purpose": PHONE,
             "value": "1 (555) 010-1234"},
        ],
        "null_case": {"field": "email", "purpose": EMAIL},
    }

    out = Path(__file__).resolve().parent / "fixtures.json"
    out.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out} (collision pair: {coll_a!r} / {coll_b!r})")


if __name__ == "__main__":
    main()
