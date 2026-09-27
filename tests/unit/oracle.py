"""Independent oracle for cross-checking the reasoning kernel.

This oracle is deliberately implemented with a *different* algorithm than
the engine under test:

* the engine compiles a meta-program and computes its well-founded model;
* this oracle enumerates abstract *arguments* (finite proof trees), builds a
  Dung attack relation and computes the **grounded extension** by its
  standard defence fixpoint.

Sharing no code with ``defeasible.engine``, agreement across many random
theories is real evidence that the kernel matches the intended
ambiguity-propagating semantics rather than its own implementation.

Argument statuses from the grounded labelling:

* ``in``       -> part of an undefeated proof      (proves its literal)
* ``out``      -> attacked by an ``in`` argument   (defeated chain)
* ``undecided``-> neither, e.g. mutual attack      (pending chain)
"""

from __future__ import annotations

from dataclasses import dataclass

from defeasible.language import RuleKind, Term, Theory


@dataclass(frozen=True)
class OracleArg:
    aid: int
    rule_id: str | None          # None => atomic fact
    kind: RuleKind | None
    conclusion: Term
    subs: tuple["OracleArg", ...]


def _ground(rules: list, evidence: list[Term]):
    """Minive grounding over every constant in evidence or rules."""
    consts = {a for e in evidence for a in e.args}
    for r in rules:
        for t in (*r.body, r.head):
            consts.update(a for a in t.args if a[:1].islower())
    consts = sorted(consts)
    out = []
    for r in rules:
        if not r.variables:
            out.append((r, r, {}))
            continue
        vs = sorted(r.variables)
        from itertools import product

        for vals in product(consts, repeat=len(vs)):
            b = dict(zip(vs, vals))
            out.append((r, r.substitute(b), b))
    return out


def build_arguments(theory: Theory, evidence: list[Term]) -> list[OracleArg]:
    """Enumerate finite proof trees, cutting positive derivation loops.

    A premise whose conclusion already occurs on the branch cannot anchor a
    finite tree there (same loop cutoff the engine uses), which keeps the
    argument set finite.  Trees are built structurally first and assigned
    integer ids afterwards so identical subtrees are shared.
    """
    from itertools import product

    facts = set(evidence) | {
        r.head
        for r in theory.rules
        if not r.body and r.is_ground and r.kind is RuleKind.STRICT
    }
    instances = _ground(theory.rules, evidence)
    rules_by_head: dict[Term, list] = {}
    for rule, gr, _b in instances:
        if gr.body:
            rules_by_head.setdefault(gr.head, []).append((rule, gr))

    # structural node: ("fact", literal) | ("rule", rule_id, tuple(child keys))
    # a key is (conclusion.literal, node) but node already determines it
    memo: dict[tuple[Term, frozenset[Term]], frozenset[tuple]] = {}

    def nodes_for(
        target: Term, on_path: frozenset[Term]
    ) -> frozenset[tuple]:
        cache_key = (target, on_path)
        if cache_key in memo:
            return memo[cache_key]
        nodes: set[tuple] = set()
        if target in facts:
            nodes.add(("fact", target.literal))
        if target not in on_path:
            for rule, gr in rules_by_head.get(target, []):
                child_sets = [
                    nodes_for(b, on_path | {target}) for b in gr.body
                ]
                if child_sets and all(child_sets):
                    for combo in product(*child_sets):
                        nodes.add(("rule", rule.id, tuple(combo)))
        memo[cache_key] = frozenset(nodes)
        return memo[cache_key]

    targets = set(facts) | set(rules_by_head)
    all_nodes: set[tuple] = set()
    node_conc: dict[tuple, Term] = {}
    for target in targets:
        for node in nodes_for(target, frozenset()):
            all_nodes.add(node)
            node_conc[node] = target

    # assign stable ids in a canonical order
    ordered = sorted(all_nodes, key=str)
    ids = {node: i for i, node in enumerate(ordered)}

    def to_arg(node: tuple) -> OracleArg:
        aid = ids[node]
        conc = node_conc[node]
        if node[0] == "fact":
            return OracleArg(aid, None, None, conc, ())
        rule_id = node[1]
        kind = next(r.kind for r in theory.rules if r.id == rule_id)
        subs = tuple(to_arg(ch) for ch in node[2])
        return OracleArg(aid, rule_id, kind, conc, subs)

    return [to_arg(n) for n in ordered]


def _dominates(theory: Theory, a: OracleArg, b: OracleArg) -> bool:
    """True iff argument a's top rule strictly dominates b's top rule."""
    if a.rule_id is None:  # strict fact
        return b.kind is RuleKind.DEFAULT
    if a.kind is RuleKind.STRICT:
        return b.kind is RuleKind.DEFAULT
    if b.rule_id is None or b.kind is RuleKind.STRICT:
        return False
    higher = {p.higher for p in theory.priorities if p.lower == a.rule_id}
    # transitive reachability a > b
    seen: set[str] = set()
    frontier = {a.rule_id}
    while frontier:
        cur = frontier.pop()
        for p in theory.priorities:
            if p.higher == cur and p.lower not in seen:
                seen.add(p.lower)
                frontier.add(p.lower)
    return b.rule_id in seen


def grounded_labelling(theory: Theory, args: list[OracleArg]) -> dict[int, str]:
    # attacks(A, B): opposite conclusions and B does NOT dominate A
    attacks: dict[int, set[int]] = {a.aid: set() for a in args}
    by_conc: dict[Term, list[OracleArg]] = {}
    for a in args:
        by_conc.setdefault(a.conclusion, []).append(a)
    for b in args:
        for a in by_conc.get(b.conclusion.opposite, []):
            if not _dominates(theory, b, a):
                attacks[b.aid].add(a.aid)

    in_set: set[int] = set()
    while True:
        added: set[int] = set()
        for a in args:
            if a.aid in in_set:
                continue
            attackers = attacks[a.aid]
            if all(any(x in in_set for x in attacks[att]) for att in attackers):
                added.add(a.aid)
        if not added:
            break
        in_set |= added

    labels: dict[int, str] = {}
    out_set: set[int] = set()
    for a in args:
        if a.aid in in_set:
            labels[a.aid] = "in"
        elif attacks[a.aid] & in_set:
            labels[a.aid] = "out"
            out_set.add(a.aid)
        else:
            labels[a.aid] = "undecided"
    return labels


def oracle_status(theory: Theory, evidence: list[Term], query: Term) -> str:
    args = build_arguments(theory, evidence)
    labels = grounded_labelling(theory, args)
    by_conc: dict[Term, list[OracleArg]] = {}
    for a in args:
        by_conc.setdefault(a.conclusion, []).append(a)

    def has_label(lit: Term, label: str) -> bool:
        return any(labels[a.aid] == label for a in by_conc.get(lit, []))

    if has_label(query, "in"):
        return "proved"
    if has_label(query.opposite, "in"):
        return "refuted"
    if has_label(query, "undecided") and has_label(query.opposite, "undecided"):
        return "conflict"
    return "unknown"
