"""The reasoning kernel: grounding, fixpoint evaluation, chain analysis.

Restricted semantics (ambiguity-propagating defeasible logic)
=============================================================

Three proof tags are computed for every ground literal ``L``:

* ``definite(L)``  -- strict proof, using facts and strict rules only.
  Strict rules and facts are **irrevocable**; if both ``L`` and ``-L`` have
  strict proofs the theory is incoherent (``TheoryConflictError``).
* ``supported(L)`` -- there exists a supporting argument chain.  A chain is
  only defeated at this level by a *strictly stronger* (``S > R``) supported
  chain for the opposite.  Two opposite defaults without a comparable
  priority therefore stay supported on both sides: the ambiguity is
  **propagated**, never silently resolved.
* ``provable(L)``  -- strong, undefeated proof.  A default chain proves ``L``
  only when every *provably applicable* opposite rule ``S`` is strictly
  weaker (``R > S``) and ``-L`` is not definite.

Resulting four-valued status for a query ``L`` (missing evidence is *never*
taken as proof of the opposite):

==================  =================================================
status              condition
==================  =================================================
``PROVED``          ``provable(L)``
``REFUTED``         ``provable(-L)`` (an explicit opposite proof)
``CONFLICT``        not provable either way, but both sides supported
``UNKNOWN``         none of the above (incl. "no evidence at all")
==================  =================================================

``supported``/``provable`` are the well-founded models of the ground
meta-program built in ``_compile_supported`` / ``_compile_provable``, so the
result is independent of rule and fact ordering by construction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from itertools import product
from typing import Iterable

from .errors import (
    InvalidInputError,
    ResourceLimitError,
    TheoryConflictError,
)
from .grounding import (
    Clause,
    EngineLimits,
    GroundRule,
    collect_domain,
    ground_theory,
    well_founded,
)
from .language import (
    RuleKind,
    Term,
    Theory,
    priority_reachable,
    validate_theory,
)

# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


class Status(str, Enum):
    PROVED = "proved"
    REFUTED = "refuted"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class ChainKind(str, Enum):
    SUPPORT = "support"   # 支持链：可用于证明结论
    DEFEAT = "defeat"     # 击败链：被更强的反方链击败
    PENDING = "pending"   # 悬而未决链：双方支持但优先级不可比 / 前提未定


@dataclass(frozen=True, slots=True)
class ChainTree:
    """A finite argument tree; ``top is None`` marks a zero-step fact."""

    top: GroundRule | None
    conclusion: Term
    children: tuple["ChainTree", ...]

    def flatten_steps(self) -> list[dict]:
        steps: list[dict] = []
        for child in self.children:
            steps.extend(child.flatten_steps())
        if self.top is not None:
            steps.append(self.top.to_step())
        return steps


@dataclass(frozen=True, slots=True)
class Chain:
    chain_id: str
    tree: ChainTree
    kind: ChainKind
    reason: str
    attacker: ChainTree | None

    @property
    def conclusion(self) -> Term:
        return self.tree.conclusion

    def to_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "kind": self.kind.value,
            "conclusion": self.conclusion.literal,
            "reason": self.reason,
            "steps": self.tree.flatten_steps(),
            "attacker_top_rule": self.attacker.top.rule_id if self.attacker and self.attacker.top else None,
            "attacker_steps": self.attacker.flatten_steps() if self.attacker else [],
        }


@dataclass(slots=True)
class QueryResult:
    literal: Term
    status: Status
    definite: bool
    supported: bool
    opposite_supported: bool
    chains: list[Chain]

    def to_dict(self) -> dict:
        buckets: dict[str, list[dict]] = {
            ChainKind.SUPPORT.value: [],
            ChainKind.DEFEAT.value: [],
            ChainKind.PENDING.value: [],
        }
        for c in self.chains:
            buckets[c.kind.value].append(c.to_dict())
        return {
            "literal": self.literal.literal,
            "status": self.status.value,
            "flags": {
                "definite": self.definite,
                "supported": self.supported,
                "opposite_supported": self.opposite_supported,
            },
            "chains": buckets,
        }


@dataclass(slots=True)
class Evaluation:
    theory_fingerprint: str
    domain: tuple[str, ...]
    evidence: tuple[Term, ...]
    ground_rule_count: int
    rounds_used: int
    conclusions: dict[Term, Status]
    ctx: "_EvalContext" = field(repr=False)

    def query(self, literal: str | Term) -> QueryResult:
        term = literal if isinstance(literal, Term) else Term.parse(literal)
        if not term.is_ground:
            raise InvalidInputError(
                f"query {term.literal!r} must be ground (no variables)"
            )
        return self.ctx.engine.explain(self.ctx, term)

    def to_dict(self) -> dict:
        return {
            "theory_fingerprint": self.theory_fingerprint,
            "domain": list(self.domain),
            "evidence": [e.literal for e in self.evidence],
            "ground_rule_count": self.ground_rule_count,
            "rounds_used": self.rounds_used,
            "conclusions": [
                {"literal": lit.literal, "status": st.value}
                for lit, st in sorted(
                    self.conclusions.items(), key=lambda kv: kv[0].literal
                )
            ],
        }



# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _EvalContext:
    theory: Theory
    facts: frozenset[Term]
    by_head: dict[Term, list[GroundRule]]
    definite: frozenset[Term]
    argument_closure: frozenset[Term]  # literals with at least one finite chain
    supported: frozenset[str]
    provable: frozenset[str]
    stronger: dict[str, frozenset[str]]
    engine: "Engine"

    def key_supported(self, lit: Term) -> bool:
        return ("sup:" + lit.literal) in self.supported

    def key_provable(self, lit: Term) -> bool:
        return ("prov:" + lit.literal) in self.provable

    def is_stronger_rule(self, s: str, r: str) -> bool:
        return Engine._is_stronger(self.stronger, s, r)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    def __init__(self, limits: EngineLimits | None = None) -> None:
        self.limits = limits or EngineLimits()

    # ----- public API -----

    def evaluate(
        self, theory: Theory, evidence: Iterable[Term] | None = None
    ) -> Evaluation:
        validate_theory(theory)
        evidence_tuple = tuple(evidence or ())
        for ev in evidence_tuple:
            if not ev.is_ground:
                raise InvalidInputError(
                    f"evidence {ev.literal!r} must be ground (no variables)"
                )

        grounded = ground_theory(theory, evidence_tuple, self.limits)
        domain = collect_domain(theory, evidence_tuple)
        facts = frozenset(evidence_tuple) | frozenset(
            gr.head
            for gr in grounded
            if gr.kind is RuleKind.STRICT and not gr.body
        )

        by_head: dict[Term, list[GroundRule]] = {}
        for gr in grounded:
            if gr.body:
                by_head.setdefault(gr.head, []).append(gr)

        stronger = priority_reachable(theory)

        # 1. definite (strict) closure and strict-coherence check
        definite, strict_rounds = self._strict_closure(facts, by_head)
        for lit in definite:
            if not lit.negated and lit.opposite in definite:
                raise TheoryConflictError(
                    "strict contradiction: opposite literals both have strict "
                    "proofs",
                    details={"literals": [lit.literal, lit.opposite.literal]},
                )

        # literals with at least one finite argument chain rooted in facts
        argument_closure = self._argument_closure(facts, by_head)

        # 2/3. one well-founded model carrying BOTH layers.  The provable
        # layer reads *supported* opposite applicability, which is what makes
        # ambiguity propagate instead of being blocked.
        program, universe = self._compile_meta_program(
            facts, by_head, definite, stronger
        )
        wf_true, _, rounds_wf = well_founded(program, universe, self.limits)
        sup_true = frozenset(k for k in wf_true if k.startswith("sup:"))
        prov_true = frozenset(k for k in wf_true if k.startswith("prov:"))

        ctx = _EvalContext(
            theory=theory,
            facts=facts,
            by_head=by_head,
            definite=definite,
            argument_closure=argument_closure,
            supported=sup_true,
            provable=prov_true,
            stronger=stronger,
            engine=self,
        )

        interesting: set[Term] = set(facts) | set(definite)
        for key in sup_true:
            if key.startswith("sup:"):
                lit = Term.parse(key.split(":", 1)[1])
                interesting.add(lit)
                interesting.add(lit.opposite)

        conclusions = {lit: self._status(ctx, lit) for lit in interesting}

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "theory": theory.to_dict(),
                    "evidence": sorted(e.literal for e in facts),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]

        return Evaluation(
            theory_fingerprint=fingerprint,
            domain=domain,
            evidence=tuple(sorted(facts, key=lambda t: t.literal)),
            ground_rule_count=len(grounded),
            rounds_used=strict_rounds + rounds_wf,
            conclusions=conclusions,
            ctx=ctx,
        )

    # ----- closures -----

    @staticmethod
    def _strict_closure(
        facts: frozenset[Term], by_head: dict[Term, list[GroundRule]]
    ) -> tuple[frozenset[Term], int]:
        definite: set[Term] = set(facts)
        rounds = 0
        while True:
            rounds += 1
            added = False
            for rules in by_head.values():
                for gr in rules:
                    if (
                        gr.kind is RuleKind.STRICT
                        and gr.head not in definite
                        and all(b in definite for b in gr.body)
                    ):
                        definite.add(gr.head)
                        added = True
            if not added:
                return frozenset(definite), rounds

    @staticmethod
    def _argument_closure(
        facts: frozenset[Term], by_head: dict[Term, list[GroundRule]]
    ) -> frozenset[Term]:
        """Least set of literals reachable from facts via any rule.

        Membership means "a finite argument chain rooted in evidence exists";
        it bounds chain enumeration so positive rule cycles cannot diverge.
        """
        reachable: set[Term] = set(facts)
        while True:
            added = False
            for head, rules in by_head.items():
                if head in reachable:
                    continue
                if any(all(b in reachable for b in gr.body) for gr in rules):
                    reachable.add(head)
                    added = True
            if not added:
                return frozenset(reachable)

    @staticmethod
    def _is_stronger(stronger: dict[str, frozenset[str]], s: str, r: str) -> bool:
        """``s`` beats ``r``: explicit priority, or ``s`` is a strict rule."""
        if s == r:
            return False
        if s not in stronger:  # strict rule ids are absent from the map
            return True
        return r in stronger.get(s, frozenset())

    # ----- meta-program compilation -----

    def _compile_meta_program(
        self,
        facts: frozenset[Term],
        by_head: dict[Term, list[GroundRule]],
        definite: frozenset[Term],
        stronger: dict[str, frozenset[str]],
    ) -> tuple[dict[str, list[Clause]], frozenset[str]]:
        """Compile both proof layers into one ground normal program.

        ``sup:L``  -- supporting argument for L (only a *strictly stronger*
                      supported opposite can defeat it)
        ``prov:L`` -- undefeated proof of L

        The decisive ambiguity-propagation rule: ``prov:L``'s beat condition
        for default R fires on an opposite rule S whose body is merely
        **supported** whenever R does not dominate S.  Hence a literal
        disputed at the support level blocks proofs built on it, and the
        dispute is passed upward instead of being settled by accident.
        """
        program: dict[str, list[Clause]] = {}
        universe: set[str] = set()
        head_lits = set(by_head) | set(facts) | set(definite)

        for lit in head_lits:
            sup_key = "sup:" + lit.literal
            prov_key = "prov:" + lit.literal
            universe.update((sup_key, prov_key))
            sup_clauses: list[Clause] = []
            prov_clauses: list[Clause] = []

            if lit in definite:
                # definite truth is irrevocable at both layers
                sup_clauses.append(((), ()))
                prov_clauses.append(((), ()))

            if lit.opposite not in definite:
                for gr in by_head.get(lit, ()):
                    sup_body = tuple("sup:" + b.literal for b in gr.body)
                    prov_body = tuple("prov:" + b.literal for b in gr.body)

                    if gr.kind is RuleKind.STRICT:
                        sup_clauses.append((sup_body, ()))
                        prov_clauses.append((prov_body, ()))
                        continue

                    # --- supported layer: beaten only by a strictly
                    #     stronger supported opposite ---
                    beat_sup = f"beat_sup:{gr.gid}"
                    universe.add(beat_sup)
                    sup_clauses.append((sup_body, (beat_sup,)))
                    for opp in by_head.get(lit.opposite, ()):
                        if self._is_stronger(
                            stronger, opp.rule_id, gr.rule_id
                        ):
                            program.setdefault(beat_sup, []).append(
                                (
                                    tuple(
                                        "sup:" + b.literal for b in opp.body
                                    ),
                                    (),
                                )
                            )
                    program.setdefault(beat_sup, [])

                    # --- provable layer: any *supported-applicable*
                    #     opposite this rule does not dominate beats it
                    #     (ambiguity propagation) ---
                    beat_prov = f"beat_prov:{gr.gid}"
                    universe.add(beat_prov)
                    prov_clauses.append((prov_body, (beat_prov,)))
                    for opp in by_head.get(lit.opposite, ()):
                        if not self._is_stronger(
                            stronger, gr.rule_id, opp.rule_id
                        ):
                            program.setdefault(beat_prov, []).append(
                                (
                                    tuple(
                                        "sup:" + b.literal for b in opp.body
                                    ),
                                    (),
                                )
                            )
                    program.setdefault(beat_prov, [])

            program.setdefault(sup_key, []).extend(sup_clauses)
            program.setdefault(prov_key, []).extend(prov_clauses)

        # Every referenced atom is in the universe; an atom with no clause
        # and no fact is then placed in the greatest unfounded set (false),
        # rather than wrongly left unknown.
        for clauses in program.values():
            for pos, neg in clauses:
                universe.update(pos)
                universe.update(neg)

        return program, frozenset(universe)

    # ----- status -----

    def _status(self, ctx: _EvalContext, lit: Term) -> Status:
        if lit in ctx.definite or ctx.key_provable(lit):
            return Status.PROVED
        if ctx.key_provable(lit.opposite):
            return Status.REFUTED
        if ctx.key_supported(lit) and ctx.key_supported(lit.opposite):
            return Status.CONFLICT
        return Status.UNKNOWN

    # ----- explanation: chain enumeration and classification -----

    def explain(self, ctx: _EvalContext, lit: Term) -> QueryResult:
        trees = self._build_trees(ctx, lit)
        chains: list[Chain] = []
        for idx, tree in enumerate(trees):
            chains.append(self._classify(ctx, tree, idx))
        return QueryResult(
            literal=lit,
            status=self._status(ctx, lit),
            definite=lit in ctx.definite,
            supported=ctx.key_supported(lit),
            opposite_supported=ctx.key_supported(lit.opposite),
            chains=chains,
        )

    def _build_trees(self, ctx: _EvalContext, lit: Term) -> list[ChainTree]:
        """All finite argument trees for ``lit`` rooted in evidence facts.

        Positive rule cycles are broken by ``on_path``: a premise already
        appearing on the branch closes a loop and cannot anchor a finite
        tree there.  Total constructed-tree count is budget-bounded so a
        combinatorial blow-up becomes ``ResourceLimitError``.
        """
        budget = _ChainBudget(self.limits.max_chains)

        def build(target: Term, on_path: frozenset[Term]) -> list[ChainTree]:
            trees: list[ChainTree] = []
            if target in ctx.facts:
                trees.append(ChainTree(top=None, conclusion=target, children=()))
            if target in on_path:
                # loop on this branch: only a zero-step fact tree is finite
                return trees
            for gr in ctx.by_head.get(target, ()):
                if any(b not in ctx.argument_closure for b in gr.body):
                    continue
                child_options = [build(b, on_path | {target}) for b in gr.body]
                if any(not opts for opts in child_options):
                    continue
                for combo in product(*child_options):
                    budget.tick(target)
                    trees.append(
                        ChainTree(
                            top=gr, conclusion=target, children=tuple(combo)
                        )
                    )
            return trees

        return build(lit, frozenset())

    def _classify(
        self, ctx: _EvalContext, tree: ChainTree, index: int
    ) -> Chain:
        cid = f"c{index + 1}"

        if tree.top is None:
            return Chain(
                chain_id=cid,
                tree=tree,
                kind=ChainKind.SUPPORT,
                reason=f"严格事实：{tree.conclusion.literal} 直接由证据给出，不可撤销",
                attacker=None,
            )

        top = tree.top
        rule_desc = f"规则 {top.rule_id}（{top.kind.value}）"
        child_chains = [
            self._classify(ctx, child, i)
            for i, child in enumerate(tree.children)
        ]

        # 1. a defeated premise defeats the whole chain outright
        defeated_child = next(
            (c for c in child_chains if c.kind is ChainKind.DEFEAT), None
        )
        if defeated_child is not None:
            return Chain(
                chain_id=cid,
                tree=tree,
                kind=ChainKind.DEFEAT,
                reason=(
                    f"{rule_desc} 的前提链已被击败（子链 "
                    f"{defeated_child.chain_id}），本链随之失败"
                ),
                attacker=defeated_child.attacker,
            )

        opposite_trees = self._build_trees(ctx, tree.conclusion.opposite)

        # 2. a *supported-applicable* strictly-stronger opposite chain beats it
        for opp in opposite_trees:
            if not self._tree_applicable(ctx, opp, ChainLevel.SUPPORTED):
                continue
            if self._compare(ctx, top.rule_id, opp) < 0:
                opp_word = (
                    "严格事实" if opp.top is None
                    else f"{'严格' if opp.top.kind is RuleKind.STRICT else '默认'}"
                         f"规则 {opp.top.rule_id}"
                )
                return Chain(
                    chain_id=cid,
                    tree=tree,
                    kind=ChainKind.DEFEAT,
                    reason=(
                        f"{rule_desc} 被反方{opp_word}击败：反方前提成立且严格更强"
                        f"（严格规则/事实恒强于默认规则，或存在显式优先关系）"
                    ),
                    attacker=opp,
                )

        # 3. premises provable and head provable: winning support chain
        if all(ctx.key_provable(b) for b in top.body) and ctx.key_provable(
            tree.conclusion
        ):
            kind_word = "严格" if top.kind is RuleKind.STRICT else "默认可撤销"
            return Chain(
                chain_id=cid,
                tree=tree,
                kind=ChainKind.SUPPORT,
                reason=(
                    f"{kind_word}支持链：{rule_desc} 全部前提可证，且不存在"
                    f"可适用且未被本方支配的反方规则"
                ),
                attacker=None,
            )

        # 4. an undecided premise makes the chain pending
        pending_child = next(
            (c for c in child_chains if c.kind is ChainKind.PENDING), None
        )
        if pending_child is not None:
            return Chain(
                chain_id=cid,
                tree=tree,
                kind=ChainKind.PENDING,
                reason=(
                    f"{rule_desc} 的前提悬而未决（子链 {pending_child.chain_id}），"
                    f"本链暂不能证明结论"
                ),
                attacker=None,
            )

        # 5. incomparable applicable opposite chains: ambiguity propagates
        for level, level_word in (
            (ChainLevel.PROVABLE, "前提可证"),
            (ChainLevel.SUPPORTED, "前提有支持"),
        ):
            for opp in opposite_trees:
                if not self._tree_applicable(ctx, opp, level):
                    continue
                if self._compare(ctx, top.rule_id, opp) == 0:
                    opp_id = opp.top.rule_id if opp.top else "(事实)"
                    opp_kind = (
                        "严格" if opp.top is None
                                   or opp.top.kind is RuleKind.STRICT
                        else "默认"
                    )
                    return Chain(
                        chain_id=cid,
                        tree=tree,
                        kind=ChainKind.PENDING,
                        reason=(
                            f"{rule_desc} 与反方{opp_kind}规则 {opp_id} 结论相反、"
                            f"{level_word}但优先级不可比：冲突保留，"
                            f"不按规则出现顺序裁决"
                        ),
                        attacker=opp,
                    )

        # 6. mutual support attack: neither side is supported (WF undefined)
        if all(ctx.key_supported(b) for b in top.body) and not ctx.key_supported(
            tree.conclusion
        ):
            return Chain(
                chain_id=cid,
                tree=tree,
                kind=ChainKind.PENDING,
                reason=(
                    f"{rule_desc} 的前提虽有支持，但与反方链相互攻击形成支持环，"
                    f"双方在良基模型中均不成立，结论悬置"
                ),
                attacker=None,
            )

        # 7. remaining finite rooted chain: plain support
        kind_word = "严格" if top.kind is RuleKind.STRICT else "默认可撤销"
        return Chain(
            chain_id=cid,
            tree=tree,
            kind=ChainKind.SUPPORT,
            reason=f"{kind_word}支持链：{rule_desc} 的前提有有限证据链支持",
            attacker=None,
        )

    @staticmethod
    def _tree_applicable(
        ctx: _EvalContext, tree: ChainTree, level: "ChainLevel"
    ) -> bool:
        """Whether a rule tree is applicable at the given proof level.

        Only premises are inspected: a rule can be applicable even when its
        own head is beaten by a still-stronger rule (team defeat -- the
        beaten rule still knocks out weaker rivals).
        """
        tag = ctx.key_provable if level is ChainLevel.PROVABLE else ctx.key_supported
        if tree.top is None:
            return tree.conclusion in ctx.facts
        return all(tag(b) for b in tree.top.body) and all(
            Engine._tree_applicable(ctx, c, level) for c in tree.children
        )

    @staticmethod
    def _compare(ctx: _EvalContext, rule_id: str, opp: ChainTree) -> int:
        """Compare this rule against an opposite tree's top rule.

        ``1`` this dominates; ``-1`` opponent dominates; ``0`` incomparable.
        Priority is never guessed: no transitive relation means ``0``.
        Facts/strict rules dominate defaults; strict-vs-strict is left at
        ``0`` here (definite strict contradictions are rejected at
        evaluation time instead).
        """
        if opp.top is None:
            return -1 if rule_id in ctx.stronger else 0
        opp_id = opp.top.rule_id
        this_default = rule_id in ctx.stronger
        opp_default = opp.top.kind is RuleKind.DEFAULT
        if not this_default and not opp_default:
            return 0
        if not this_default:
            return 1
        if not opp_default:
            return -1
        if opp_id in ctx.stronger.get(rule_id, frozenset()):
            return 1
        if rule_id in ctx.stronger.get(opp_id, frozenset()):
            return -1
        return 0


class ChainLevel(Enum):
    SUPPORTED = "supported"
    PROVABLE = "provable"


@dataclass(slots=True)
class _ChainBudget:
    limit: int
    used: int = 0

    def tick(self, target: Term) -> None:
        self.used += 1
        if self.used > self.limit:
            raise ResourceLimitError(
                f"argument-chain enumeration exceeded max_chains={self.limit}",
                details={"limit": self.limit, "literal": target.literal},
            )
