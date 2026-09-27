"""The four failure categories must stay distinguishable."""

from __future__ import annotations

import pytest

from defeasible.engine import Engine, EngineLimits
from defeasible.errors import (
    ComputationError,
    InvalidInputError,
    ResourceLimitError,
    TheoryConflictError,
)
from defeasible.language import Term, Theory


def test_malformed_literal_is_invalid_input() -> None:
    with pytest.raises(InvalidInputError) as exc:
        Engine().evaluate(Theory(), [Term.parse("not a literal")])
    assert exc.value.code == "invalid_input"
    assert exc.value.http_status == 422


def test_non_ground_evidence_is_invalid_input() -> None:
    t = Theory()
    t.add_rule("r", "default", ["bird(X)"], "flies(X)")
    with pytest.raises(InvalidInputError) as exc:
        Engine().evaluate(t, [Term.parse("bird(X)")])
    assert exc.value.code == "invalid_input"


def test_non_ground_query_is_invalid_input() -> None:
    ev = Engine().evaluate(Theory(), [])
    with pytest.raises(InvalidInputError):
        ev.query("flies(X)")


def test_priority_cycle_is_state_conflict() -> None:
    t = Theory()
    t.add_rule("a", "default", ["p(X)"], "q(X)")
    t.add_rule("b", "default", ["r(X)"], "-q(X)")
    t.add_priority("a", "b")
    t.add_priority("b", "a")
    with pytest.raises(TheoryConflictError) as exc:
        Engine().evaluate(t, [Term.parse("p(t)"), Term.parse("r(t)")])
    assert exc.value.code == "state_conflict"
    assert exc.value.http_status == 409


def test_strict_contradiction_is_state_conflict() -> None:
    t = Theory()
    t.add_rule("s1", "strict", ["p(X)"], "q(X)")
    t.add_rule("s2", "strict", ["p(X)"], "-q(X)")
    with pytest.raises(TheoryConflictError) as exc:
        Engine().evaluate(t, [Term.parse("p(t)")])
    assert exc.value.code == "state_conflict"


def test_grounding_explosion_is_resource_exhausted() -> None:
    t = Theory()
    for i, c in enumerate("abcdefgh"):
        t.add_fact(f"c{i}({c})", fact_id=f"f{i}")
    t.add_rule(
        "big",
        "default",
        ["c0(X)", "c1(Y)", "c2(Z)", "c3(W)"],
        "q(X, Y, Z, W)",
    )
    with pytest.raises(ResourceLimitError) as exc:
        Engine(EngineLimits(max_ground_rules=500)).evaluate(t, [])
    assert exc.value.code == "resource_exhausted"
    assert exc.value.http_status == 509
    assert exc.value.details["rule_id"] == "big"


def test_chain_enumeration_budget_is_resource_exhausted() -> None:
    # many independent ways to derive the same head -> combinatorial trees
    t = Theory()
    t.add_fact("base", fact_id="fb")
    for i in range(120):
        t.add_fact(f"a{i}", fact_id=f"fa{i}")
        t.add_rule(f"r{i}", "default", [f"a{i}"], "p")
    t.add_rule("combine", "default", ["p", "base"], "q")
    ev = Engine(EngineLimits(max_chains=100)).evaluate(t, [])
    with pytest.raises(ResourceLimitError) as exc:
        ev.query("q")  # chain enumeration happens at explanation time
    assert exc.value.code == "resource_exhausted"


def test_computation_failure_is_separate_category() -> None:
    err = ComputationError("boom")
    assert err.code == "computation_failure"
    assert err.http_status == 500


def test_codes_are_pairwise_distinct() -> None:
    codes = {
        InvalidInputError().code,
        TheoryConflictError().code,
        ResourceLimitError().code,
        ComputationError().code,
    }
    assert codes == {
        "invalid_input",
        "state_conflict",
        "resource_exhausted",
        "computation_failure",
    }
