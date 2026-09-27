"""Typed errors raised by the Rete engine.

Every failure path raises one of these so callers (API layer, tests) can
assert on a specific failure category instead of catching generic exceptions.
"""

from __future__ import annotations


class ReteError(Exception):
    """Base class for all engine errors."""


class RuleError(ReteError):
    """A rule definition is malformed (bad condition, unbound variable, ...)."""


class RuleNotFoundError(ReteError):
    """A rule name was referenced that is not loaded in the network."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"rule not found: {name!r}")


class DuplicateRuleError(ReteError):
    """A rule with the same name is already loaded."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"duplicate rule: {name!r}")


class FactNotFoundError(ReteError):
    """A retract referenced a fact that is not in working memory."""

    def __init__(self, kind: str, fields: tuple):
        self.kind = kind
        self.fields = tuple(fields)
        super().__init__(f"fact not found: {kind}{self.fields!r}")


class CycleLimitError(ReteError):
    """run() was called with an invalid max_cycles bound."""
