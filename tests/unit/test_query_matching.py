"""变量查询模板匹配: 常量过滤、同名变量一致、置换去重。"""
from __future__ import annotations

from app.kernel.facade import answer_query, compile_knowledge_base
from app.rulelang import parse_query, parse_theory


def _compile(text, limits):
    theory = parse_theory(text)
    return compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )


def test_constant_argument_filters_matches(settings):
    compiled = _compile(
        """
        Bird(tweety). Bird(polly).
        @r Flies(X) := Bird(X).
        """,
        settings.limits,
    )
    # 接地查询, 常量不匹配 => 一个 no_evidence 答案 (未知, 不是被否决)
    miss = answer_query(compiled, parse_query("Flies(ghost)"), max_chains=64)
    assert len(miss) == 1
    assert miss[0].status == "no_evidence"
    assert miss[0].reason_code == "NO_EVIDENCE_AT_ALL"

    # 常量匹配 => 一个答案
    hit = answer_query(compiled, parse_query("Flies(tweety)"), max_chains=64)
    assert len(hit) == 1
    assert hit[0].status == "accepted"


def test_repeated_variable_must_bind_consistently(settings):
    compiled = _compile(
        """
        Edge(a, b). Edge(b, c). Edge(a, a).
        @path Path(X, Y) := Edge(X, Y).
        """,
        settings.limits,
    )
    # X 与 Y 同名变量 => 只回带 X=Y 的置换
    answers = answer_query(compiled, parse_query("Path(X, X)"), max_chains=64)
    substs = [a.substitution for a in answers]
    assert substs == [{"X": "a"}]


def test_distinct_variables_enumerate_all(settings):
    compiled = _compile(
        """
        Edge(a, b). Edge(b, c).
        @path Path(X, Y) := Edge(X, Y).
        """,
        settings.limits,
    )
    answers = answer_query(compiled, parse_query("Path(X, Y)"), max_chains=64)
    pairs = {(a.substitution["X"], a.substitution["Y"]) for a in answers}
    assert pairs == {("a", "b"), ("b", "c")}


def test_negated_template_matches_only_negated(settings):
    compiled = _compile(
        """
        Bird(t). Penguin(t).
        @r1 Flies(X) := Bird(X).
        @r2 -Flies(X) := Penguin(X).
        priority(r2, r1).
        """,
        settings.limits,
    )
    pos = answer_query(compiled, parse_query("Flies(X)"), max_chains=64)
    neg = answer_query(compiled, parse_query("-Flies(X)"), max_chains=64)
    assert {a.goal for a in pos} == {"Flies(t)"}
    assert {a.goal for a in neg} == {"-Flies(t)"}

