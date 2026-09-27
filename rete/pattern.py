"""Rule language: conditions, tests, actions, rules.

A rule is pure data (JSON-serialisable) so it can be submitted over the HTTP
API, stored, and replayed.  Variables are strings starting with "?".

Example (dict form)::

    {
      "name": "vip-big-order",
      "salience": 10,
      "conditions": [
        {"kind": "customer", "fields": ["?cid", "vip"]},
        {"kind": "order",    "fields": ["?oid", "?cid", "?amount"]}
      ],
      "tests": [[">", "?amount", 1000]],
      "actions": [
        {"op": "assert", "kind": "flag", "fields": ["?oid", "high-value"]},
        {"op": "emit",   "tag": "flagged", "fields": ["?oid"]}
      ]
    }
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any

from .errors import RuleError

VAR_PREFIX = "?"

# Canonical operator names plus accepted aliases.
OPS = {
    "eq": operator.eq, "==": operator.eq,
    "ne": operator.ne, "!=": operator.ne,
    "lt": operator.lt, "<": operator.lt,
    "le": operator.le, "<=": operator.le,
    "gt": operator.gt, ">": operator.gt,
    "ge": operator.ge, ">=": operator.ge,
}

ALLOWED_SCALARS = (str, int, float, bool, type(None))


def is_var(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(VAR_PREFIX)


def _check_scalar(value: Any, where: str) -> None:
    if is_var(value):
        return
    if not isinstance(value, ALLOWED_SCALARS):
        raise RuleError(f"{where}: unsupported field value {value!r}; "
                        f"only scalars and '?var' variables are allowed")


@dataclass(frozen=True)
class Condition:
    """One pattern: a fact kind plus per-field constants or variables."""

    kind: str
    fields: tuple = ()

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind:
            raise RuleError("condition kind must be a non-empty string")
        object.__setattr__(self, "fields", tuple(self.fields))
        for i, f in enumerate(self.fields):
            _check_scalar(f, f"condition {self.kind} field {i}")

    @property
    def variables(self) -> tuple:
        seen, out = set(), []
        for f in self.fields:
            if is_var(f) and f not in seen:
                seen.add(f)
                out.append(f)
        return tuple(out)

    @property
    def constant_tests(self) -> tuple:
        """(position, value) pairs for constant fields — drives alpha sharing."""
        return tuple((i, f) for i, f in enumerate(self.fields) if not is_var(f))


@dataclass(frozen=True)
class Test:
    """A comparison between two operands (variables or constants)."""

    op: str
    a: Any
    b: Any

    def __post_init__(self):
        if self.op not in OPS:
            raise RuleError(f"unknown test operator {self.op!r}; "
                            f"allowed: {sorted(OPS)}")

    @property
    def variables(self) -> tuple:
        return tuple(v for v in (self.a, self.b) if is_var(v))

    def evaluate(self, bindings: dict) -> bool:
        return OPS[self.op](resolve(self.a, bindings), resolve(self.b, bindings))


def resolve(operand: Any, bindings: dict) -> Any:
    if is_var(operand):
        if operand not in bindings:
            raise RuleError(f"unbound variable {operand!r} in test/action")
        return bindings[operand]
    return operand


@dataclass(frozen=True)
class Action:
    """A rule right-hand-side step.

    op == "assert":  insert fact (kind, fields) after variable substitution.
    op == "retract": retract one occurrence of the resolved fact.
    op == "emit":    append a record to the engine's output sink (observable
                     side effect used by tests and integrations).
    """

    op: str
    kind: str | None = None
    fields: tuple = ()
    tag: str | None = None

    def __post_init__(self):
        if self.op not in ("assert", "retract", "emit"):
            raise RuleError(f"unknown action op {self.op!r}; "
                            f"allowed: assert, retract, emit")
        object.__setattr__(self, "fields", tuple(self.fields))
        if self.op in ("assert", "retract"):
            if not self.kind or is_var(self.kind):
                raise RuleError(f"action {self.op}: kind must be a literal string")
        if self.op == "emit" and not self.tag:
            raise RuleError("action emit: tag is required")

    @property
    def variables(self) -> tuple:
        return tuple(f for f in self.fields if is_var(f))


@dataclass(frozen=True)
class Rule:
    name: str
    conditions: tuple
    actions: tuple
    salience: int = 0
    tests: tuple = ()

    def __post_init__(self):
        if not self.name:
            raise RuleError("rule name must be non-empty")
        if not self.conditions:
            raise RuleError(f"rule {self.name!r}: at least one condition required")
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "tests", tuple(self.tests))
        bound: set = set()
        for cond in self.conditions:
            bound.update(cond.variables)
        for t in self.tests:
            for v in t.variables:
                if v not in bound:
                    raise RuleError(
                        f"rule {self.name!r}: test {t!r} references unbound {v!r}")
        for act in self.actions:
            for v in act.variables:
                if v not in bound:
                    raise RuleError(
                        f"rule {self.name!r}: action references unbound {v!r}")


# ---------------------------------------------------------------------------
# Dict (JSON) parsing
# ---------------------------------------------------------------------------

def condition_from_dict(d: object) -> Condition:
    if not isinstance(d, dict):
        raise RuleError(f"condition must be an object, got {type(d).__name__}")
    if "kind" not in d:
        raise RuleError("condition missing key 'kind'")
    raw_fields = d.get("fields", ())
    if not isinstance(raw_fields, (list, tuple)):
        raise RuleError("condition 'fields' must be a list")
    return Condition(kind=d["kind"], fields=tuple(raw_fields))


def test_from_seq(seq: object) -> Test:
    if not isinstance(seq, (list, tuple)):
        raise RuleError(f"test must be a list [op, a, b], got {seq!r}")
    parts = list(seq)
    if len(parts) != 3:
        raise RuleError(f"test must be [op, a, b], got {parts!r}")
    return Test(op=str(parts[0]), a=parts[1], b=parts[2])


def action_from_dict(d: object) -> Action:
    if not isinstance(d, dict):
        raise RuleError(f"action must be an object, got {type(d).__name__}")
    if "op" not in d:
        raise RuleError("action missing key 'op'")
    raw_fields = d.get("fields", ())
    if not isinstance(raw_fields, (list, tuple)):
        raise RuleError("action 'fields' must be a list")
    return Action(op=d["op"], kind=d.get("kind"),
                  fields=tuple(raw_fields), tag=d.get("tag"))


def rule_from_dict(d: dict) -> Rule:
    """Parse and validate the JSON rule language.

    Every malformed input raises :class:`RuleError` (never KeyError or
    TypeError), so callers get one named failure category."""
    if not isinstance(d, dict):
        raise RuleError("rule must be an object")

    def _require(key: str, expected: type | tuple):
        if key not in d:
            raise RuleError(f"rule missing key {key!r}")
        value = d[key]
        if not isinstance(value, expected):
            want = (expected if isinstance(expected, tuple) else (expected,))
            names = "/".join(t.__name__ for t in want)
            raise RuleError(
                f"rule {key!r} must be {names}, got {type(value).__name__}")
        return value

    name = _require("name", str)
    raw_conditions = _require("conditions", list)
    raw_tests = d.get("tests", [])
    raw_actions = d.get("actions", [])
    if not isinstance(raw_tests, list):
        raise RuleError("rule 'tests' must be a list")
    if not isinstance(raw_actions, list):
        raise RuleError("rule 'actions' must be a list")

    try:
        conditions = tuple(condition_from_dict(c) for c in raw_conditions)
        tests = tuple(test_from_seq(t) for t in raw_tests)
        actions = tuple(action_from_dict(a) for a in raw_actions)
    except RuleError:
        raise
    salience = d.get("salience", 0)
    if not isinstance(salience, int):
        raise RuleError("salience must be an integer")
    return Rule(name=name, conditions=conditions, actions=actions,
                salience=salience, tests=tests)
