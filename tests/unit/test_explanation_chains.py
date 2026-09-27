"""解释层: 链树结构必须完整、自洽, 支持/击败/悬置三类可区分。"""
from __future__ import annotations

from app.kernel.facade import answer_query, answer_to_dict, compile_knowledge_base
from app.rulelang import parse_query, parse_theory


def _compile(text, limits):
    theory = parse_theory(text)
    return compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )


def _answer(text, query, limits):
    compiled = _compile(text, limits)
    return answer_to_dict(
        answer_query(compiled, parse_query(query), max_chains=64)[0]
    )


def test_supporting_chain_tree_ends_in_facts(settings):
    ans = _answer(
        """
        Bird(t).
        @r Flies(X) := Bird(X).
        """,
        "Flies(t)",
        settings.limits,
    )
    assert ans["status"] == "accepted"
    chain = ans["supporting_chains"][0]
    tree = chain["tree"]
    assert tree["conclusion"] == "Flies(t)"
    assert tree["rule"] == "r"
    premise = tree["premises"][0]
    assert premise["rule_kind"] == "fact"
    assert premise["conclusion"] == "Bird(t)"
    assert premise["premises"] == []


def test_defeated_chain_names_priority_basis(settings):
    ans = _answer(
        """
        Bird(t). Penguin(t).
        @r1 Flies(X) := Bird(X).
        @r2 -Flies(X) := Penguin(X).
        priority(r2, r1).
        """,
        "Flies(t)",
        settings.limits,
    )
    chain = ans["defeated_chains"][0]
    counter = chain["counter_chains"][0]
    assert counter["attack_kind"] == "REBUTTAL"
    assert counter["rule"] == "r2"
    assert "r2 > r1" in counter["detail"]
    # 击败者的树本身也要完整
    assert counter["tree"]["premises"][0]["conclusion"] == "Penguin(t)"


def test_pending_chain_has_counter_and_no_decisive_winner(settings):
    ans = _answer(
        """
        Q(n). R(n).
        @q P(X) := Q(X).
        @r -P(X) := R(X).
        """,
        "P(n)",
        settings.limits,
    )
    assert ans["status"] == "undecided"
    assert ans["chain_counts"] == {"supporting": 0, "defeated": 0, "pending": 1}
    chain = ans["pending_chains"][0]
    assert chain["counter_chains"][0]["detail"]
    assert ans["supporting_chains"] == []
    assert ans["defeated_chains"] == []


def test_no_evidence_has_no_chains_but_is_not_false(settings):
    ans = _answer("Bird(t).", "Ghost(t)", settings.limits)
    assert ans["status"] == "no_evidence"
    assert ans["reason_code"] == "NO_EVIDENCE_AT_ALL"
    assert ans["chain_counts"] == {"supporting": 0, "defeated": 0, "pending": 0}


def test_opposing_evidence_listed_when_goal_has_no_chain(settings):
    # 只能推出 -Dangerous, Dangerous 本身无任何链 => rejected 且给出反方证据
    ans = _answer(
        """
        Herbivore(h).
        @r -Dangerous(X) := Herbivore(X).
        """,
        "Dangerous(h)",
        settings.limits,
    )
    assert ans["status"] == "rejected"
    assert ans["reason_code"] == "REJECTED_OPPOSITE_GROUNDED"
    assert ans["chain_counts"] == {"supporting": 0, "defeated": 0, "pending": 0}
    assert len(ans["opposing_evidence"]) == 1
    assert ans["opposing_evidence"][0]["conclusion"] == "-Dangerous(h)"
    assert ans["opposing_evidence"][0]["attack_kind"] == "OPPOSITE_GROUNDED"


def test_three_buckets_are_disjoint_and_exhaustive(settings):
    # 多冲突并存: 一个已决 (有优先), 一个悬置; 断言三类桶互斥
    ans = _answer(
        """
        K(m). J(m).
        Q(n). R(n).
        @k S(X) := K(X).
        @j -S(X) := J(X).
        @q P(X) := Q(X).
        @r -P(X) := R(X).
        priority(k, j).
        """,
        "S(m)",
        settings.limits,
    )
    # S(m) 有优先 => accepted
    assert ans["status"] == "accepted"
    assert len(ans["supporting_chains"]) == 1
    assert len(ans["defeated_chains"]) == 0
    assert len(ans["pending_chains"]) == 0
