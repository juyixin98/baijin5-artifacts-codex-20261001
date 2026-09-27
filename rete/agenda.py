"""Conflict agenda: activations ordered by priority then a stable key.

Ordering contract (deterministic, documented):
  1. higher rule salience first;
  2. then rule name (lexicographic);
  3. then the tuple of fact ids in condition order.

An activation's identity is ``(rule name, wme id tuple)`` — it is bound to
the exact fact combination that produced it, so retraction of any
participating fact removes precisely this activation.
"""

from __future__ import annotations

from .beta import Token
from .pattern import Rule


class Activation:
    __slots__ = ("rule", "token")

    def __init__(self, rule: Rule, token: Token):
        self.rule = rule
        self.token = token

    @property
    def identity(self) -> tuple:
        return (self.rule.name, self.token.wme_ids)

    @property
    def sort_key(self) -> tuple:
        return (-self.rule.salience, self.rule.name, self.token.wme_ids)

    def to_dict(self, fact_keys: dict) -> dict:
        return {
            "rule": self.rule.name,
            "salience": self.rule.salience,
            "fact_ids": list(self.token.wme_ids),
            "fact_keys": [list(fact_keys[i]) for i in self.token.wme_ids
                          if i in fact_keys],
            "bindings": dict(self.token.bindings),
        }


class Agenda:
    def __init__(self):
        self._activations: dict[tuple, Activation] = {}

    def add(self, rule: Rule, token: Token) -> None:
        act = Activation(rule, token)
        self._activations[act.identity] = act

    def remove(self, rule: Rule, token: Token) -> None:
        self._activations.pop((rule.name, token.wme_ids), None)

    def pop(self) -> Activation:
        """Remove and return the highest-priority activation."""
        key = min(self._activations, key=lambda k: self._activations[k].sort_key)
        return self._activations.pop(key)

    def peek_order(self) -> list[Activation]:
        return [self._activations[k] for k in
                sorted(self._activations, key=lambda k: self._activations[k].sort_key)]

    def __len__(self) -> int:
        return len(self._activations)

    def __bool__(self) -> bool:
        return bool(self._activations)
