"""Regression tests for facts satisfying more than one condition.

A single WME may occupy several positions of one match (self-join). The
match must be produced exactly once, retraction must delete it exactly
once, and re-insertion must rebuild it.
"""

from __future__ import annotations

import logging

from conftest import assert_match_sets_equal
from reference import reference_matches
from rete import Engine

log = logging.getLogger("tests.selfjoin")


def _ids(engine):
    return sorted(tuple(m["fact_ids"]) for m in engine.matches())


def _ref(engine):
    facts = [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
    return reference_matches(
        [{"name": "s", "conditions": [
            {"kind": "a", "fields": ["?x"]},
            {"kind": "a", "fields": ["?y"]}]}], facts)


def test_same_fact_satisfying_two_conditions_matches_once_and_retracts():
    engine = Engine()
    engine.add_rule({"name": "s", "conditions": [
        {"kind": "a", "fields": ["?x"]},
        {"kind": "a", "fields": ["?x"]}], "actions": []})
    w, _ = engine.insert("a", ("k",))
    assert _ids(engine) == [(w.id, w.id)]
    assert len(engine.agenda) == 1
    engine.retract("a", ("k",))
    assert _ids(engine) == []
    assert len(engine.agenda) == 0
    log.info("single-fact self-join: one match, one activation, clean retract")


def test_two_facts_cross_join_full_timeline_matches_reference():
    engine = Engine()
    engine.add_rule({"name": "s", "conditions": [
        {"kind": "a", "fields": ["?x"]},
        {"kind": "a", "fields": ["?y"]}], "actions": []})
    w1, _ = engine.insert("a", (1,))
    w2, _ = engine.insert("a", (2,))
    assert _ids(engine) == sorted([
        (w1.id, w1.id), (w1.id, w2.id), (w2.id, w1.id), (w2.id, w2.id)])
    assert_match_sets_equal(engine.matches(), _ref(engine), "selfjoin n=2")

    engine.retract("a", (1,))
    assert _ids(engine) == [(w2.id, w2.id)]
    assert_match_sets_equal(engine.matches(), _ref(engine), "selfjoin retract1")

    w3, _ = engine.insert("a", (1,))
    assert _ids(engine) == sorted([
        (w3.id, w3.id), (w3.id, w2.id), (w2.id, w3.id), (w2.id, w2.id)])
    assert_match_sets_equal(engine.matches(), _ref(engine), "selfjoin readd")

    engine.retract("a", (2,))
    assert _ids(engine) == [(w3.id, w3.id)]
    engine.retract("a", (1,))
    assert _ids(engine) == []
    log.info("self-join timeline (insert x2, retract, re-add, retract x2) "
             "matched the independent oracle at every checkpoint")


def test_self_join_with_join_variable_requires_equal_fields():
    engine = Engine()
    engine.add_rule({"name": "pair", "conditions": [
        {"kind": "e", "fields": ["?a", "?b"]},
        {"kind": "e", "fields": ["?b", "?c"]}], "actions": []})
    engine.insert("e", (1, 2))
    engine.insert("e", (2, 3))
    engine.insert("e", (4, 5))
    facts = [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
    assert_match_sets_equal(
        engine.matches(),
        reference_matches([{"name": "pair", "conditions": [
            {"kind": "e", "fields": ["?a", "?b"]},
            {"kind": "e", "fields": ["?b", "?c"]}]}], facts),
        "chained self-join")
    # Only 1->2->3 chains: (1,2)+(2,3). The (4,5) edge has no successor.
    assert len(engine.matches()) == 1
    m = engine.matches()[0]
    assert m["fact_keys"] == [["e", 1, 2], ["e", 2, 3]]
    engine.retract("e", (2, 3))
    assert engine.matches() == []
    log.info("path-style self-join: only true chains match; retract clears")
