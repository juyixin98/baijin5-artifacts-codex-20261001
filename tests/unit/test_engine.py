"""Concrete engine tests: specific statuses, chains, and rule-order invariance."""

from __future__ import annotations

import itertools
import random

import pytest

from defeasible.engine import ChainKind, Engine, EngineLimits, Status
from defeasible.errors import (
    InvalidInputError,
    ResourceLimitError,
    TheoryConflictError,
)
from defeasible.language import RuleKind, Term, Theory

from .oracle import oracle_status

E = lambda: Engine()  # noqa: E731


def birds_theory() -> Theory:
    t = Theory()
    t.add_fact("bird(tweety)", fact_id="f_bt")
    t.add_rule("r_bird", "default", ["bird(X)"], "flies(X)")
    t.add_rule("r_peng", "default", ["penguin(X)"], "-flies(X)")
    t.add_rule("s_pb", "strict", ["penguin(X)"], "bird(X)")
    t.add_priority("r_peng", "r_bird")
    return t


POLLY_EVIDENCE = [Term.parse("penguin(polly)")]


class TestBirds:
    def test_plain_bird_flies(self) -> None:
        ev = E().evaluate(birds_theory(), POLLY_EVIDENCE)
        r = ev.query("flies(tweety)")
        assert r.status is Status.PROVED
        assert r.definite is False  # defeasible, not strict
        assert r.supported is True
        assert r.opposite_supported is False
        kinds = {c.kind for c in r.chains}
        assert kinds == {ChainKind.SUPPORT}

    def test_penguin_does_not_fly_via_higher_priority(self) -> None:
        ev = E().evaluate(birds_theory(), POLLY_EVIDENCE)
        r = ev.query("flies(polly)")
        assert r.status is Status.REFUTED
        assert r.opposite_supported is True
        # the defeated chain names the higher-priority attacker
        defeated = [c for c in r.chains if c.kind is ChainKind.DEFEAT]
        assert defeated, "expected a defeated chain for flies(polly)"
        assert defeated[0].attacker is not None
        assert defeated[0].attacker.top.rule_id == "r_peng"

    def test_negated_head_is_proved(self) -> None:
        ev = E().evaluate(birds_theory(), POLLY_EVIDENCE)
        r = ev.query("-flies(polly)")
        assert r.status is Status.PROVED

    def test_missing_evidence_is_unknown_not_refuted(self) -> None:
        # Rule 3: absence of evidence is never the opposite fact.
        ev = E().evaluate(birds_theory(), POLLY_EVIDENCE)
        r = ev.query("flies(ghost)")
        assert r.status is Status.UNKNOWN
        assert r.supported is False and r.opposite_supported is False
        assert r.chains == []


class TestNixonDiamond:
    def theory(self) -> Theory:
        t = Theory()
        t.add_rule("r_q", "default", ["quaker(X)"], "pacifist(X)")
        t.add_rule("r_rep", "default", ["republican(X)"], "-pacifist(X)")
        return t

    def test_conflict_is_retained_without_priority(self) -> None:
        evidence = [Term.parse("quaker(nixon)"), Term.parse("republican(nixon)")]
        ev = E().evaluate(self.theory(), evidence)
        for lit, opp in [("pacifist(nixon)", "-pacifist(nixon)")]:
            r = ev.query(lit)
            assert r.status is Status.CONFLICT
            assert r.supported and r.opposite_supported
            pend = [c for c in r.chains if c.kind is ChainKind.PENDING]
            assert pend and "不可比" in pend[0].reason
            assert ev.query(opp).status is Status.CONFLICT

    def test_adding_priority_resolves_conflict(self) -> None:
        t = self.theory()
        t.add_priority("r_q", "r_rep")
        evidence = [Term.parse("quaker(nixon)"), Term.parse("republican(nixon)")]
        ev = E().evaluate(t, evidence)
        assert ev.query("pacifist(nixon)").status is Status.PROVED
        assert ev.query("-pacifist(nixon)").status is Status.REFUTED


class TestStrictRules:
    def test_strict_rule_beats_default_without_priority(self) -> None:
        t = Theory()
        t.add_rule("d", "default", ["p(X)"], "q(X)")
        t.add_rule("s", "strict", ["p(X)"], "-q(X)")
        ev = E().evaluate(t, [Term.parse("p(t)")])
        assert ev.query("q(t)").status is Status.REFUTED
        assert ev.query("-q(t)").status is Status.PROVED

    def test_strict_contradiction_is_state_conflict(self) -> None:
        t = Theory()
        t.add_rule("s1", "strict", ["p(X)"], "q(X)")
        t.add_rule("s2", "strict", ["p(X)"], "-q(X)")
        with pytest.raises(TheoryConflictError) as exc:
            E().evaluate(t, [Term.parse("p(t)")])
        assert exc.value.code == "state_conflict"
        assert set(exc.value.details["literals"]) == {"q(t)", "-q(t)"}

    def test_strict_fact_chain_is_irrevocable(self) -> None:
        t = Theory()
        t.add_fact("closed", fact_id="f")
        t.add_rule("d", "default", ["open(X)"], "closed(X)")
        ev = E().evaluate(t, [])
        r = ev.query("closed")
        assert r.status is Status.PROVED
        assert r.chains[0].tree.top is None  # zero-step fact chain


class TestTeamDefeat:
    def test_stronger_team_member_protects_conclusion(self) -> None:
        t = Theory()
        t.add_rule("r1", "default", ["a(X)"], "p(X)")
        t.add_rule("r2", "default", ["b(X)"], "-p(X)")
        t.add_rule("r3", "default", ["c(X)"], "p(X)")
        t.add_priority("r2", "r1")
        t.add_priority("r3", "r2")
        ev = E().evaluate(
            t, [Term.parse(x) for x in ("a(t)", "b(t)", "c(t)")]
        )
        assert ev.query("p(t)").status is Status.PROVED
        # r1 chain is beaten while r3 chain supplies the winning support
        ids_support = {
            c.tree.top.rule_id
            for c in ev.query("p(t)").chains
            if c.kind is ChainKind.SUPPORT
        }
        ids_defeat = {
            c.tree.top.rule_id
            for c in ev.query("p(t)").chains
            if c.kind is ChainKind.DEFEAT
        }
        assert "r3" in ids_support
        assert "r1" in ids_defeat


class TestOrderIndependence:
    """Rule/fact/priority reordering must never change conclusions."""

    def _theory(self) -> Theory:
        t = Theory()
        t.add_fact("bird(tweety)", fact_id="f1")
        t.add_fact("penguin(polly)", fact_id="f2")
        t.add_fact("quaker(nixon)", fact_id="f3")
        t.add_fact("republican(nixon)", fact_id="f4")
        t.add_rule("rb", "default", ["bird(X)"], "flies(X)")
        t.add_rule("rp", "default", ["penguin(X)"], "-flies(X)")
        t.add_rule("spb", "strict", ["penguin(X)"], "bird(X)")
        t.add_rule("rq", "default", ["quaker(X)"], "pacifist(X)")
        t.add_rule("rr", "default", ["republican(X)"], "-pacifist(X)")
        t.add_priority("rp", "rb")
        return t

    def test_all_permutations_of_conflicting_rules_agree(self) -> None:
        # Enumerate every ordering of the four rules that actually interact
        # (4! = 24 evaluations) -- small enough to exhaust fully.
        base = E().evaluate(self._theory(), [])
        baseline = {k.literal: v for k, v in base.conclusions.items()}
        full = self._theory()
        ids = ["rb", "rp", "spb", "rq"]
        chosen = [next(r for r in full.rules if r.id == i) for i in ids]
        rest = [r for r in full.rules if r.id not in ids]
        for perm in itertools.permutations(chosen):
            shuffled = Theory(
                rules=list(perm) + rest, priorities=list(full.priorities)
            )
            ev = E().evaluate(shuffled, [])
            table = {k.literal: v for k, v in ev.conclusions.items()}
            assert table == baseline

    def test_random_shuffles_agree_with_baseline(self) -> None:
        base = E().evaluate(self._theory(), [])
        baseline = {k.literal: v.value for k, v in base.conclusions.items()}
        for seed in range(30):
            random.seed(seed)
            t = self._theory()
            random.shuffle(t.rules)
            random.shuffle(t.priorities)
            table = {
                k.literal: v.value
                for k, v in E().evaluate(t, []).conclusions.items()
            }
            assert table == baseline


class TestOracleAgreement:
    """Kernel status must match the independent grounded-extension oracle."""

    @pytest.mark.parametrize("seed", range(25))
    def test_random_small_theories(self, seed: int) -> None:
        rng = random.Random(seed)
        consts = ["t"]
        preds = ["p", "q", "r", "s"]
        t = Theory()
        rule_no = 0
        rule_ids: list[str] = []
        for _ in range(rng.randint(1, 4)):
            rid = f"g{rule_no}"
            rule_no += 1
            bp = rng.choice(preds)
            hp = rng.choice(preds)
            neg = rng.random() < 0.4
            t.add_rule(
                rid,
                RuleKind.DEFAULT,
                [f"{bp}(X)"],
                ("-" if neg else "") + f"{hp}(X)",
            )
            rule_ids.append(rid)
        # add a random acyclic priority chain on a subset
        subset = rng.sample(rule_ids, k=min(2, len(rule_ids)))
        if len(subset) == 2:
            t.add_priority(subset[0], subset[1])
        evidence = [
            Term.parse(f"{p}(t)")
            for p in rng.sample(preds, k=rng.randint(1, len(preds)))
        ]
        ev = E().evaluate(t, evidence)
        for hp in preds:
            for lit in (f"{hp}(t)", f"-{hp}(t)"):
                term = Term.parse(lit)
                engine_status = ev.query(term).status.value
                assert engine_status == oracle_status(t, evidence, term), (
                    f"seed={seed} query={lit}"
                )
