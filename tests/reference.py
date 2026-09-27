"""Independent brute-force reference matcher.

This module is deliberately written from scratch and imports NOTHING from
the ``rete`` package. It is the test oracle: the production engine's
incremental Rete network must produce exactly the match set this naive
enumerator produces.

Semantics mirrored (the contract under test, re-derived independently):
  * A distinct fact is the content key ``(kind, fields...)``. Repeated
    insertions of identical content are ONE fact for matching purposes.
  * A rule matches when one fact per condition unifies (variables bind
    consistently, constants equal) and every comparison test holds.
  * Fact combinations are enumerated with replacement (the same WME may
    satisfy more than one condition), which is what the network permits.
"""

from __future__ import annotations

import itertools
from typing import Any

VAR_PREFIX = "?"


def _is_var(v: Any) -> bool:
    return isinstance(v, str) and v.startswith(VAR_PREFIX)


def _condition_matches(cond: dict, fact: tuple) -> bool:
    kind, *fields = fact
    if kind != cond["kind"]:
        return False
    spec = cond.get("fields", [])
    if len(spec) != len(fields):
        return False
    return all(_is_var(s) or s == f for s, f in zip(spec, fields))


def _unify(rule: dict, combo: list[tuple]) -> dict | None:
    bindings: dict = {}
    for cond, fact in zip(rule["conditions"], combo):
        spec = cond.get("fields", [])
        for spec_v, fact_v in zip(spec, fact[1:]):
            if _is_var(spec_v):
                if spec_v in bindings and bindings[spec_v] != fact_v:
                    return None
                bindings[spec_v] = fact_v
    return bindings


_OPS = {
    "==": lambda a, b: a == b, "eq": lambda a, b: a == b,
    "!=": lambda a, b: a != b, "ne": lambda a, b: a != b,
    "<": lambda a, b: a < b, "lt": lambda a, b: a < b,
    "<=": lambda a, b: a <= b, "le": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "gt": lambda a, b: a > b,
    ">=": lambda a, b: a >= b, "ge": lambda a, b: a >= b,
}


def _resolve(operand: Any, bindings: dict) -> Any:
    return bindings[operand] if _is_var(operand) else operand


def _tests_hold(rule: dict, bindings: dict) -> bool:
    for op, a, b in rule.get("tests", []):
        try:
            if not _OPS[op](_resolve(a, bindings), _resolve(b, bindings)):
                return False
        except TypeError:
            # Comparison between unorderable types: the test cannot hold.
            return False
    return True


def reference_matches(rules: list[dict], facts: list[tuple]) -> set[tuple]:
    """Return the set of ``(rule_name, fact_key_tuple, binding_tuple)``.

    ``facts`` is a list of distinct content keys ``(kind, f1, f2, ...)``.
    """
    out: set[tuple] = set()
    for rule in rules:
        pools = [
            [f for f in facts if _condition_matches(cond, f)]
            for cond in rule["conditions"]
        ]
        if any(not p for p in pools):
            continue
        for combo in itertools.product(*pools):
            bindings = _unify(rule, list(combo))
            if bindings is None:
                continue
            if not _tests_hold(rule, bindings):
                continue
            out.add((
                rule["name"],
                tuple(combo),
                tuple(sorted(bindings.items())),
            ))
    return out
