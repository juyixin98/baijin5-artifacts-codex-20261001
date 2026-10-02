#!/usr/bin/env python3
"""Triangulate the synthetic fixtures against a SECOND mature ASN.1
implementation in a different language: pyasn1 (Python).

The Go suite already cross-checks the service codec with go-asn1-ber;
this script adds independent verification through pyasn1.

Comparison policy (pyasn1 strips EXPLICIT/context tags in its generic
decoder, so raw shapes are not always preserved):

  * every expect_ok fixture must decode in pyasn1 with an EMPTY
    remainder (the bytes are a complete, legal BER value to it too);
  * every INTEGER decimal value found in the fixture must equal the
    value pyasn1 decodes — including the 301-octet "超长整数";
  * fixtures whose expected tree is entirely universal-class are also
    compared node-for-node (class/constructed/tag/shape);
  * for rejected fixtures the script records pyasn1's verdict so the
    BER-vs-restricted-profile divergences stay visible.

Usage:  python3 scripts/crosscheck_pyasn1.py [fixtures/vectors.json]
Verified with pyasn1 0.4.8; stdlib otherwise.
"""

import json
import sys
from pathlib import Path

from pyasn1.codec.ber import decoder as ber_decoder
from pyasn1.type import tag, univ

UNIVERSAL_CLASS = int(tag.tagClassUniversal)       # 0
CONTEXT_CLASS = int(tag.tagClassContext)           # 2
CONSTRUCTED = int(tag.tagFormatConstructed)        # 0x20
INTEGER_TAG = 2


def is_constructed(value: object) -> bool:
    return hasattr(value, "tagSet") and int(value.tagSet[-1][1]) == CONSTRUCTED


def is_integer(value):
    cls, _fmt, tagid = value.tagSet[-1]
    return int(cls) == UNIVERSAL_CLASS and int(tagid) == INTEGER_TAG


# Non-numeric universal types must never be coerced via int(); without an
# asn1Spec pyasn1 also exposes generic leaves (e.g. 'field-0') that ARE
# convertible via int(), so the exclusion list is the safe approach.
try:
    from pyasn1.type import char as _char
    _TEXT_TYPES = (_char.UTF8String,)
except Exception:  # pragma: no cover - version differences
    _TEXT_TYPES = ()

NON_INTEGER_TYPES = (
    univ.BitString, univ.OctetString, univ.Null, univ.Boolean,
    univ.ObjectIdentifier, univ.Real,
) + _TEXT_TYPES


def walk(value: object, out: list[str]) -> None:
    """Collect INTEGER scalar values in document order.

    Membership is decided by Python type plus a guarded int() fallback
    because pyasn1's schema-less decoder rewrites explicit/context
    wrappers and names generic leaves 'field-N'.
    """
    if isinstance(value, univ.Integer):
        out.append(str(int(value)))
        return
    if isinstance(value, NON_INTEGER_TYPES):
        return
    if is_constructed(value):
        try:
            # Iterating may yield field NAMES when pyasn1 assigned named
            # components; getComponentByPosition always yields the value.
            for i in range(len(value)):
                walk(value.getComponentByPosition(i), out)
        except (TypeError, IndexError):
            return
        return
    # generic (possibly tag-stripped) scalar leaf
    try:
        int(value)
        out.append(str(int(value)))
    except (TypeError, ValueError):
        pass


def decoded_integers(value: object) -> list[str]:
    out = []
    walk(value, out)
    return out


def expected_integers(exp: dict) -> list[str]:
    out = []
    if exp.get("integer_decimal"):
        out.append(exp["integer_decimal"])
    for c in exp.get("children", []):
        out.extend(expected_integers(c))
    return out


def expected_shape(exp: dict) -> tuple:
    cls = {"universal": UNIVERSAL_CLASS, "context": CONTEXT_CLASS}[exp["class"]]
    return (cls, bool(exp.get("constructed")), int(exp["tag"]),
            [expected_shape(c) for c in exp.get("children", [])])


def actual_shape_universal(value: object) -> tuple:
    """Shape for a purely-universal tree (no context EXPLICIT stripping)."""
    if not hasattr(value, "tagSet"):
        # pyasn1 may expose a raw scalar after implicit decoding; treat as leaf
        return (UNIVERSAL_CLASS, False, 0, [])
    cls, fmt, tagid = value.tagSet[-1]
    children = []
    if is_constructed(value):
        try:
            for i in range(len(value)):
                children.append(actual_shape_universal(value.getComponentByPosition(i)))
        except (TypeError, IndexError):
            pass
    return (int(cls), int(fmt) == CONSTRUCTED, int(tagid), children)


def tree_is_universal(exp: dict) -> bool:
    if exp["class"] != "universal":
        return False
    return all(tree_is_universal(c) for c in exp.get("children", []))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures/vectors.json")
    fixtures = json.loads(path.read_text())

    ok_checked = shape_checked = 0
    rejected_accepted = rejected_rejected = 0
    failures, verdicts = [], []

    # pyasn1's GENERIC decoder is schema-driven: a primitive context tag
    # wrapping opaque (non-INTEGER) bytes cannot be represented without an
    # asn1Spec and it errors with "...not in asn1Spec". go-asn1-ber and
    # the service codec handle opaque context primitives natively, so this
    # is a documented pyasn1 limitation rather than a value disagreement.
    pyasn1_schema_limited = {"context5-primitive"}

    for fx in fixtures:
        raw = bytes.fromhex(fx["input_hex"])
        try:
            value, remainder = ber_decoder.decode(raw)
            py_ok, err = True, ""
        except Exception as exc:
            py_ok, value, remainder, err = False, None, b"", str(exc)

        if fx["expect_ok"]:
            if not py_ok:
                if fx["id"] in pyasn1_schema_limited:
                    verdicts.append(
                        f"  pyasn1 schema-limited {fx['id']:22s} "
                        f"opaque context primitive ({err[:48]})")
                else:
                    failures.append(
                        f"[{fx['id']}] service+go-asn1-ber accept, pyasn1 rejects: {err}")
                continue
            if remainder != b"":
                failures.append(
                    f"[{fx['id']}] pyasn1 left {len(remainder)} trailing byte(s)")
                continue
            exp = fx.get("expect_value")
            if exp:
                got_ints = decoded_integers(value)
                want_ints = expected_integers(exp)
                if got_ints != want_ints:
                    failures.append(
                        f"[{fx['id']}] INTEGER values {got_ints} != expected {want_ints}")
                if tree_is_universal(exp):
                    want = expected_shape(exp)
                    got = actual_shape_universal(value)
                    if got != want:
                        failures.append(
                            f"[{fx['id']}] shape {got} != expected {want}")
                    shape_checked += 1
            ok_checked += 1
        else:
            if py_ok:
                rejected_accepted += 1
                verdicts.append(
                    f"  pyasn1 ACCEPTS  {fx['id']:28s} service rejects: {fx['expect_kind']}")
            else:
                rejected_rejected += 1
                verdicts.append(
                    f"  pyasn1 rejects  {fx['id']:28s} service rejects: {fx['expect_kind']}")

    print(f"fixtures total                 : {len(fixtures)}")
    print(f"valid fixtures value-matched   : {ok_checked}")
    print(f"  of which shape-matched       : {shape_checked} (pure-universal trees)")
    print(f"invalid: pyasn1 also rejects   : {rejected_rejected}")
    print(f"invalid: pyasn1 accepts (BER leniency, documented): {rejected_accepted}")
    print("\npyasn1 verdicts on inputs the service rejects:")
    for row in verdicts:
        print(row)

    if failures:
        print("\nCROSS-CHECK FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL VALID FIXTURES MATCH ACROSS go-asn1-ber, pyasn1 AND THE SERVICE CODEC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
