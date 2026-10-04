"""Independent plaintext-reference verifier.

This module MUST NOT import anything from ``sensitive_layer``. It re-derives
every expectation from the plaintext fixtures using only the Python standard
library (unicodedata/hmac/hashlib), so the service's answers are checked
against an independently computed reference — not against the service's own
implementation of the same code.

Checks performed over HTTP against a running service:
  1. every fixture record can be stored;
  2. every fixture query returns exactly the record set derived here by
     plaintext comparison (including same-value-different-representation
     cases and the forced short-index collision case, where the colliding
     record must be filtered out by decrypt-confirmation);
  3. the stored blind index of probe records equals the HMAC-SHA256 value
     computed HERE with stdlib hmac (determinism / honest-derivation check);
  4. NULL handling: NULL queries are rejected with the typed category;
  5. an interrupted index-key rotation followed by a resume never changes
     any query answer (dual-version querying must not miss records).
"""
from __future__ import annotations

import hashlib
import hmac
import unicodedata


# --- independent re-implementation of the normalization spec (stdlib only) ---

def normalize(value: str, field_type: str) -> str:
    if field_type == "email":
        out = unicodedata.normalize("NFKC", value).strip().casefold()
        if any(ch.isspace() for ch in out):
            raise ValueError("email contains whitespace")
    elif field_type == "phone":
        s = unicodedata.normalize("NFKC", value).strip()
        digits = "".join(ch for ch in s if ch.isdigit())
        out = ("+" if s.startswith("+") else "") + digits
    elif field_type == "name":
        out = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    elif field_type == "raw":
        out = unicodedata.normalize("NFKC", value)
    else:
        raise ValueError(f"unknown field_type: {field_type}")
    if out == "":
        raise ValueError("empty after normalization")
    return out


def _lp(data: bytes) -> bytes:
    return len(data).to_bytes(2, "big") + data


def blind_index_hex(key_hex: str, purpose: str, norm_version: str,
                    normalized: str, bits: int) -> str:
    """Stdlib-only recomputation of the blind index spec."""
    msg = (b"BI\x01" + _lp(purpose.encode()) + _lp(norm_version.encode())
           + _lp(normalized.encode()))
    digest = hmac.new(bytes.fromhex(key_hex), msg, hashlib.sha256).digest()
    nbytes = (bits + 7) // 8
    out = bytearray(digest[:nbytes])
    if bits % 8:
        out[-1] &= (0xFF << (8 - bits % 8)) & 0xFF
    return bytes(out).hex()


def expected_matches(records: list[dict], field: str, purpose: str,
                     field_type: str, value: str) -> list[str]:
    """Reference answer: plaintext equality after independent normalization."""
    target = normalize(value, field_type)
    ids = [
        r["record_id"]
        for r in records
        if r["field"] == field and r["purpose"] == purpose
        and r["value"] is not None
        and normalize(r["value"], field_type) == target
    ]
    return sorted(ids)


# --- HTTP verification flow ---

def run_verification(client, fixtures: dict) -> dict:
    """client: anything with .get/.post/.put returning (status_code, .json()),
    e.g. httpx.Client or fastapi.testclient.TestClient."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    field_types = fixtures["field_types"]

    def run_query(q) -> dict | None:
        resp = client.post("/query", json={
            "field": q["field"], "purpose": q["purpose"], "value": q["value"]})
        if resp.status_code != 200:
            return None
        return resp.json()["result"]

    # 1. store all records
    for rec in fixtures["records"]:
        resp = client.put(f"/records/{rec['record_id']}", json={
            "field": rec["field"], "purpose": rec["purpose"],
            "value": rec["value"]})
        check(f"put:{rec['record_id']}",
              resp.status_code == 200 and resp.json().get("ok") is True,
              f"status={resp.status_code}")

    # 2. query answers match the plaintext reference
    def check_queries(tag: str, check_filtered: bool) -> None:
        for q in fixtures["queries"]:
            expected = expected_matches(
                fixtures["records"], q["field"], q["purpose"],
                field_types[q["purpose"]], q["value"])
            result = run_query(q)
            got = result["confirmed"] if result else None
            check(f"{tag}query:{q['name']}",
                  got == expected == q["expected_confirmed"],
                  f"got={got} expected={expected}")
            # The forced collision only exists under the ORIGINAL index key;
            # after rotation the new key separates the pair again, so the
            # filtered-candidate assertion applies pre- and mid-rotation only.
            if check_filtered and q.get("expect_filtered") and result is not None:
                check(f"{tag}query:{q['name']}:collision-filtered",
                      result["filtered_candidates"] >= 1,
                      f"filtered={result['filtered_candidates']}")

    check_queries("", check_filtered=True)

    # 3. stored indexes match stdlib-computed HMAC (honest derivation)
    for probe in fixtures["index_probes"]:
        resp = client.get("/admin/indexes", params={
            "record_id": probe["record_id"], "field": probe["field"],
            "purpose": probe["purpose"]})
        stored = [i["index_hex"] for i in resp.json()["result"]["indexes"]]
        want = blind_index_hex(
            fixtures["index_key_v1"], probe["purpose"], fixtures["norm_version"],
            normalize(probe["value"], field_types[probe["purpose"]]),
            fixtures["index_bits"])
        check(f"index-probe:{probe['record_id']}", want in stored,
              f"stored={stored} want={want}")

    # 4. NULL is not queryable, and the failure is typed
    resp = client.post("/query", json={
        "field": fixtures["null_case"]["field"],
        "purpose": fixtures["null_case"]["purpose"], "value": None})
    body = resp.json()
    check("null-query-rejected",
          resp.status_code == 422
          and body["error"]["category"] == "null_not_indexable",
          f"status={resp.status_code} body={body}")

    # 5. interrupted rotation: answers must not change mid-rotation or after
    resp = client.post("/admin/rotate-index-key", json={"crash_after": 1})
    mid = resp.json()["result"]
    check("rotation-interrupted", mid["rotation"]["status"] == "rotating",
          f"state={mid['rotation']}")
    check_queries("mid-rotation:", check_filtered=True)
    resp = client.post("/admin/rotation/resume")
    done = resp.json()["result"]
    check("rotation-resume-completes", done["rotation"]["status"] == "idle",
          f"state={done['rotation']}")
    check_queries("post-rotation:", check_filtered=False)

    passed = sum(1 for c in checks if c["ok"])
    return {"checks": checks, "passed": passed, "failed": len(checks) - passed}
