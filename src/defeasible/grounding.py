"""Grounding and the well-founded fixpoint used by the reasoning kernel.

Kept separate from ``engine.py`` so that the kernel file (status policy and
chain explanations) and the model-theoretic machinery each stay small.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable

from .errors import InvalidInputError, ResourceLimitError
from .language import RuleKind, Term, Theory


@dataclass(frozen=True, slots=True)
class EngineLimits:
    max_ground_rules: int = 100_000
    max_chains: int = 1_000
    max_rounds: int = 1_000


@dataclass(frozen=True, slots=True)
class GroundRule:
    gid: int
    rule_id: str
    kind: RuleKind
    body: tuple[Term, ...]
    head: Term
    binding: dict[str, str]

    def to_step(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind.value,
            "body": [b.literal for b in self.body],
            "head": self.head.literal,
            "binding": dict(self.binding),
        }


def collect_domain(theory: Theory, evidence: Iterable[Term]) -> tuple[str, ...]:
    """All constants appearing in evidence or ground rule parts."""
    consts: set[str] = set()
    for ev in evidence:
        consts.update(ev.args)  # evidence is ground: every arg is a constant
    for r in theory.rules:
        for t in (*r.body, r.head):
            consts.update(a for a in t.args if a[:1].islower())
    return tuple(sorted(consts))


def ground_theory(
    theory: Theory, evidence: Iterable[Term], limits: EngineLimits
) -> list[GroundRule]:
    """Instantiate every rule over the evidence domain.

    Body-less strict rules (facts embedded in the theory) are instantiated
    directly; external evidence is supplied separately, keeping the rule
    base and the evidence store decoupled.
    """
    domain = collect_domain(theory, evidence)
    grounded: list[GroundRule] = []
    gid = 0
    for rule in theory.rules:
        if not rule.body:
            if not rule.is_ground:  # defensive: validation catches this earlier
                raise InvalidInputError(f"rule {rule.id!r}: fact head must be ground")
            grounded.append(GroundRule(gid, rule.id, rule.kind, (), rule.head, {}))
            gid += 1
            continue
        variables = sorted(rule.variables)
        if not variables:
            grounded.append(GroundRule(gid, rule.id, rule.kind, rule.body, rule.head, {}))
            gid += 1
            continue
        for values in product(domain, repeat=len(variables)):
            binding = dict(zip(variables, values))
            inst = rule.substitute(binding)
            grounded.append(
                GroundRule(gid, rule.id, rule.kind, inst.body, inst.head, binding)
            )
            gid += 1
            if gid > limits.max_ground_rules:
                raise ResourceLimitError(
                    f"ground theory exceeds max_ground_rules={limits.max_ground_rules}",
                    details={
                        "limit": limits.max_ground_rules,
                        "rule_id": rule.id,
                        "domain_size": len(domain),
                    },
                )
    return grounded


# A normal-program clause is (positive atom keys, negated atom keys).
Clause = tuple[tuple[str, ...], tuple[str, ...]]


def well_founded(
    program: dict[str, list[Clause]],
    universe: frozenset[str],
    limits: EngineLimits,
) -> tuple[frozenset[str], frozenset[str], int]:
    """Well-founded model of a finite ground normal program.

    Returns (true, false, rounds), alternating the definite-consequence
    operator with removal of the greatest unfounded set.  Only set equality
    drives the fixpoint, so the result is independent of iteration order.
    """
    true: set[str] = set()
    false: set[str] = set()
    rounds = 0
    while True:
        rounds += 1
        if rounds > limits.max_rounds:
            raise ResourceLimitError(
                f"fixpoint exceeded max_rounds={limits.max_rounds}",
                details={"limit": limits.max_rounds},
            )

        # least fixpoint while `false` is held fixed
        new_true: set[str] = set()
        changed = True
        while changed:
            changed = False
            for atom, clauses in program.items():
                if atom in new_true:
                    continue
                for pos, neg in clauses:
                    if all(p in new_true for p in pos) and all(
                        n in false for n in neg
                    ):
                        new_true.add(atom)
                        changed = True
                        break

        # greatest unfounded set relative to new_true
        unfounded = set(universe)
        changed = True
        while changed:
            changed = False
            for atom in list(unfounded):
                for pos, neg in program.get(atom, ()):
                    pos_blocked = any(p in false or p in unfounded for p in pos)
                    neg_blocked = any(n in new_true for n in neg)
                    if not pos_blocked and not neg_blocked:
                        unfounded.discard(atom)
                        changed = True
                        break

        if new_true == true and (false | unfounded) == false:
            return frozenset(true), frozenset(false), rounds
        true, false = new_true, false | unfounded
