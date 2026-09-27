"""静态语义校验器的具体断言。"""
from __future__ import annotations

import pytest

from app.errors import RuleLanguageError
from app.rulelang import parse_theory


def test_head_variable_must_be_bound_by_positive_body():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory("@u Flies(X) := Bird(Y).")
    assert exc.value.category.value == "input_error"
    assert "不安全" in exc.value.message
    assert exc.value.details["unbound"] == ["X"]


def test_naf_variable_must_be_bound():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory(
            """
            Bird(t).
            @r P(X) := Bird(X), not Abnormal(Y).
            """
        )
    assert "不安全" in exc.value.message
    assert "Y" in exc.value.details["unbound"]


def test_naf_with_bound_variable_is_allowed():
    theory = parse_theory(
        """
        Bird(t).
        @r Flies(X) := Bird(X), not Wingless(X).
        """
    )
    assert len(theory.defeasible_rules) == 1


def test_naf_forbidden_in_strict_rule():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory("P. @s Q :- P, not R.")
    assert "严格规则" in exc.value.message


def test_fact_must_be_ground():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory("Bird(X).")
    assert "接地" in exc.value.message


def test_naf_fact_rejected():
    with pytest.raises(RuleLanguageError):
        parse_theory("not Bird(t).")


def test_arity_conflict_rejected():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory(
            """
            Bird(t).
            @r Flies(X) := Bird(X, Y).
            """
        )
    assert "元数冲突" in exc.value.message
    assert exc.value.details["predicate"] == "Bird"


def test_priority_must_reference_defined_rules():
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory("@a P := Q. priority(a, missing).")
    assert "不存在的规则" in exc.value.message
    assert exc.value.details["missing_rule"] == "missing"


def test_self_priority_rejected():
    with pytest.raises(RuleLanguageError):
        parse_theory("@a P := Q. priority(a, a).")


@pytest.mark.parametrize(
    "edges",
    [
        "priority(a,b). priority(b,a).",
        "priority(a,b). priority(b,c). priority(c,a).",
    ],
)
def test_priority_cycle_rejected_with_cycle_detail(edges):
    text = f"P. @a Q := P. @b R := P. @c S := P. {edges}"
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory(text)
    cycle = exc.value.details["cycle"]
    assert cycle[0] == cycle[-1]
    assert len(cycle) >= 3
