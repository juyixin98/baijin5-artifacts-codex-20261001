"""The restricted rule language.

Syntax (deliberately tiny, with a fully defined semantics in ``engine.py``)::

    strict  :  Bird(x)            => Flies(x)
    default :  Bird(x)            => Flies(x)
    default :  Penguin(x)         => -Flies(x)
    priority:  r_penguin > r_bird          # r_penguin beats r_bird

* **Terms** are atoms ``Predicate(...)`` with zero or more constants and
  variables.  Variables start with an upper-case letter, constants with a
  lower-case one.  Classical negation ``-`` is the only opposite connective.
* **Strict rules** hold without exception; two conflicting strict rules make
  the theory incoherent.
* **Default rules** are *revisable*: a default conclusion can be beaten by a
  strict fact/rule, by a higher-priority default, or by an unresolved
  conflict with an incomparable default.
* **Priority statements** ``a > b`` are *only* meaningful between defaults
  and must form an acyclic directed graph.

The language layer is pure data plus parse/dump helpers; it performs no
reasoning and holds no global state, so theories are trivially serialisable
into SQLite fixtures and API payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .errors import InvalidInputError, TheoryConflictError

# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"^[A-Z_][A-Za-z0-9_]*$")
_CONST_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_PRED_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class RuleKind(str, Enum):
    STRICT = "strict"
    DEFAULT = "default"


def is_variable(name: str) -> bool:
    return bool(_VAR_RE.match(name))


def is_constant(name: str) -> bool:
    return bool(_CONST_RE.match(name))


@dataclass(frozen=True, slots=True)
class Term:
    """A positive or classically negated ground/generic atom.

    ``negated=True`` means classical negation (``-P``), *not* negation as
    failure.  There is no "not" connective in this language: a missing atom
    is simply unknown, never the opposite fact.
    """

    predicate: str
    args: tuple[str, ...] = ()
    negated: bool = False

    # ----- constructors / canonical text -----

    @property
    def literal(self) -> str:
        """Stable textual key, e.g. ``Flies(tweety)`` or ``-Flies(x)``."""
        inner = self.predicate + (f"({', '.join(self.args)})" if self.args else "")
        return ("-" if self.negated else "") + inner

    @property
    def positive(self) -> "Term":
        """The same atom without the negation sign."""
        return Term(self.predicate, self.args, False)

    @property
    def opposite(self) -> "Term":
        """The complementary literal (``-P`` for ``P`` and vice versa)."""
        return Term(self.predicate, self.args, not self.negated)

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(a for a in self.args if is_variable(a))

    @property
    def is_ground(self) -> bool:
        return not any(is_variable(a) for a in self.args)

    def substitute(self, binding: dict[str, str]) -> "Term":
        new_args = tuple(binding.get(a, a) for a in self.args)
        return Term(self.predicate, new_args, self.negated)

    @staticmethod
    def parse(text: str) -> "Term":
        """Parse ``-Pred(a, B)`` (whitespace tolerant)."""
        text = text.strip()
        negated = text.startswith("-")
        if negated:
            text = text[1:].strip()
        m = re.match(r"^([a-zA-Z][\w]*)\s*(?:\((.*)\))?$", text)
        if not m:
            raise InvalidInputError(f"malformed literal: {text!r}")
        predicate = m.group(1)
        if not _PRED_RE.match(predicate):
            raise InvalidInputError(
                f"predicate {predicate!r} must start with a lower-case letter"
            )
        args: tuple[str, ...] = ()
        if m.group(2) is not None:
            raw_args = [a.strip() for a in m.group(2).split(",")]
            if raw_args == [""]:
                raise InvalidInputError(f"empty argument list in {text!r}")
            for a in raw_args:
                if not _ID_RE.match(a):
                    raise InvalidInputError(f"bad argument {a!r} in {text!r}")
                if not (is_variable(a) or is_constant(a)):
                    raise InvalidInputError(
                        f"argument {a!r} is neither a variable (Upper case) "
                        f"nor a constant (lower case)"
                    )
            args = tuple(raw_args)
        return Term(predicate, args, negated)


@dataclass(frozen=True, slots=True)
class Rule:
    """An implication ``body -> head`` of one kind with an explicit id."""

    id: str
    kind: RuleKind
    body: tuple[Term, ...]
    head: Term
    label: str = ""

    @property
    def variables(self) -> frozenset[str]:
        vs: set[str] = set()
        for t in self.body:
            vs |= t.variables
        vs |= self.head.variables
        return frozenset(vs)

    @property
    def is_ground(self) -> bool:
        return not self.variables

    def substitute(self, binding: dict[str, str]) -> "Rule":
        return Rule(
            id=self.id,
            kind=self.kind,
            body=tuple(t.substitute(binding) for t in self.body),
            head=self.head.substitute(binding),
            label=self.label,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "body": [t.literal for t in self.body],
            "head": self.head.literal,
            "label": self.label,
        }

    @staticmethod
    def from_dict(d: dict) -> "Rule":
        try:
            rid = str(d["id"])
            kind = RuleKind(d["kind"])
            body = tuple(Term.parse(b) for b in d["body"])
            head = Term.parse(str(d["head"]))
        except KeyError as e:
            raise InvalidInputError(f"rule is missing field {e!s}") from None
        except ValueError:
            raise InvalidInputError(
                f"rule {d.get('id')!r}: kind must be 'strict' or 'default'"
            ) from None
        label = str(d.get("label", ""))
        return Rule(id=rid, kind=kind, body=body, head=head, label=label)


@dataclass(frozen=True, slots=True)
class Priority:
    """``higher`` beats ``lower`` when both fire with opposite heads."""

    higher: str
    lower: str

    def to_dict(self) -> dict:
        return {"higher": self.higher, "lower": self.lower}

    @staticmethod
    def from_dict(d: dict) -> "Priority":
        try:
            return Priority(higher=str(d["higher"]), lower=str(d["lower"]))
        except KeyError as e:
            raise InvalidInputError(f"priority is missing field {e!s}") from None


@dataclass(slots=True)
class Theory:
    """A complete, validated knowledge base.

    Facts are stored as body-less strict rules so that strict evidence and
    strict domain rules go through exactly the same code path.
    """

    rules: list[Rule] = field(default_factory=list)
    priorities: list[Priority] = field(default_factory=list)

    # ----- convenience constructors -----

    def add_fact(self, literal: str, *, fact_id: str | None = None) -> "Theory":
        term = Term.parse(literal)
        rid = fact_id or f"fact_{len(self.rules)}"
        self.rules.append(Rule(rid, RuleKind.STRICT, (), term))
        return self

    def add_rule(
        self,
        rid: str,
        kind: RuleKind | str,
        body: list[str],
        head: str,
        *,
        label: str = "",
    ) -> "Theory":
        self.rules.append(
            Rule(
                id=rid,
                kind=kind if isinstance(kind, RuleKind) else RuleKind(kind),
                body=tuple(Term.parse(b) for b in body),
                head=Term.parse(head),
                label=label,
            )
        )
        return self

    def add_priority(self, higher: str, lower: str) -> "Theory":
        self.priorities.append(Priority(higher, lower))
        return self

    # ----- serialisation -----

    def to_dict(self) -> dict:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "priorities": [p.to_dict() for p in self.priorities],
        }

    @staticmethod
    def from_dict(d: dict) -> "Theory":
        if not isinstance(d, dict):
            raise InvalidInputError("theory must be an object")
        raw_rules = d.get("rules", [])
        raw_prios = d.get("priorities", [])
        if not isinstance(raw_rules, list) or not isinstance(raw_prios, list):
            raise InvalidInputError("'rules' and 'priorities' must be lists")
        rules = [Rule.from_dict(r) for r in raw_rules]
        prios = [Priority.from_dict(p) for p in raw_prios]
        return Theory(rules=rules, priorities=prios)


# ---------------------------------------------------------------------------
# Validation (safety + acyclicity)
# ---------------------------------------------------------------------------


def validate_theory(theory: Theory) -> None:
    """Check structural well-formedness. Raises on the first problem.

    * unique rule ids
    * rules are *range-restricted*: every variable in the head occurs in a
      positive body literal, so grounding against the evidence domain is
      well defined (we never invent constants)
    * priorities reference default rules and form an acyclic graph
    """

    ids: dict[str, Rule] = {}
    for r in theory.rules:
        if not r.id:
            raise InvalidInputError("rule id must be non-empty")
        if r.id in ids:
            raise InvalidInputError(f"duplicate rule id {r.id!r}")
        ids[r.id] = r

        # range restriction over POSITIVE body literals
        bound: set[str] = set()
        for lit in r.body:
            if not lit.negated:
                bound |= lit.variables
        unbound = r.head.variables - bound
        if unbound:
            raise InvalidInputError(
                f"rule {r.id!r}: head variables {sorted(unbound)} do not occur "
                f"in a positive body literal (rules must be range-restricted)"
            )
        # variables in a negated subgoal must also be bound positively
        for lit in r.body:
            if lit.negated and not lit.variables <= bound:
                raise InvalidInputError(
                    f"rule {r.id!r}: negated literal {lit.literal!r} contains "
                    f"variables not bound by a positive literal"
                )

    default_ids = {r.id for r in theory.rules if r.kind is RuleKind.DEFAULT}
    seen_edges: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = {}
    for p in theory.priorities:
        if p.higher not in ids or p.lower not in ids:
            raise InvalidInputError(
                f"priority {p.higher!r} > {p.lower!r} references an unknown rule"
            )
        if p.higher not in default_ids or p.lower not in default_ids:
            raise InvalidInputError(
                f"priority {p.higher!r} > {p.lower!r}: priorities may only "
                f"relate default rules (strict rules are always stronger)"
            )
        edge = (p.higher, p.lower)
        if edge in seen_edges:
            raise InvalidInputError(f"duplicate priority {p.higher!r} > {p.lower!r}")
        seen_edges.add(edge)
        graph.setdefault(p.higher, set()).add(p.lower)

    cycle = _find_cycle(graph)
    if cycle is not None:
        raise TheoryConflictError(
            "priority relation must be acyclic",
            details={"cycle": cycle},
        )


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Return one cycle as an ordered list of nodes, or ``None``."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    for ns in graph.values():
        for n in ns:
            color.setdefault(n, WHITE)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color[nxt] == GREY:
                start = stack.index(nxt)
                return stack[start:] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found is not None:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(color):
        if color[node] == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


def priority_rank(theory: Theory) -> dict[str, int]:
    """Longest-path rank of the priority DAG: bigger rank == stronger rule.

    Rules with no priority relation share a rank only when they are not
    connected; comparisons always use ``reachable`` below rather than the
    raw rank, because equal rank does *not* imply comparability.
    """
    graph: dict[str, set[str]] = {}
    nodes = {r.id for r in theory.rules if r.kind is RuleKind.DEFAULT}
    for p in theory.priorities:
        graph.setdefault(p.higher, set()).add(p.lower)
    rank: dict[str, int] = {}

    def depth(n: str, visiting: frozenset[str]) -> int:
        if n in rank:
            return rank[n]
        if n in visiting:  # defensive; validation already forbids cycles
            raise TheoryConflictError(f"priority cycle through {n!r}")
        children = graph.get(n, set())
        value = 0 if not children else 1 + max(depth(c, visiting | {n}) for c in children)
        rank[n] = value
        return value

    for n in nodes:
        depth(n, frozenset())
    return rank


def priority_reachable(theory: Theory) -> dict[str, frozenset[str]]:
    """For each default, the set of defaults it is strictly stronger than."""
    graph: dict[str, set[str]] = {}
    for p in theory.priorities:
        graph.setdefault(p.higher, set()).add(p.lower)
    out: dict[str, frozenset[str]] = {}
    for node in {r.id for r in theory.rules if r.kind is RuleKind.DEFAULT}:
        seen: set[str] = set()
        frontier = list(graph.get(node, ()))
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            frontier.extend(graph.get(cur, ()))
        out[node] = frozenset(seen)
    return out
