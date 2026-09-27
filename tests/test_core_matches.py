"""Core matching: hand-authored expectations AND the independent reference.

Every static fixture is checked three ways:
  1. exact equality with hand-written expected matches (catching a reference
     implementation that shares the engine's blind spot);
  2. exact equality with the brute-force reference matcher;
  3. negative assertions for combinations that must NOT match.
"""

from __future__ import annotations

import logging

from conftest import (assert_match_sets_equal, build_engine_from_fixture,
                      load_fixture, normalize_engine_matches)
from reference import reference_matches

log = logging.getLogger("tests.core")


def _reference_set_for(fixture: dict, engine) -> set:
    facts = [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
    return reference_matches(fixture["rules"], facts)


def test_shared_condition_matches_hand_expectations_and_reference():
    fixture = load_fixture("shared_condition.json")
    engine, _ = build_engine_from_fixture(fixture)

    got = normalize_engine_matches(engine.matches())
    expected = {
        (m["rule"],
         tuple(tuple(k) for k in m["fact_keys"]),
         tuple(sorted(m["bindings"].items())))
        for m in fixture["expected_matches"]
    }
    assert got == expected, sorted(got ^ expected)

    assert_match_sets_equal(
        engine.matches(), _reference_set_for(fixture, engine),
        context="shared_condition")
    log.info("shared_condition: %d matches verified against both oracles",
             len(got))


def test_shared_condition_fire_order_is_salience_then_stable_key():
    fixture = load_fixture("shared_condition.json")
    engine, _ = build_engine_from_fixture(fixture)
    result = engine.run(20)

    order = [f.rule for f in result.fired]
    assert order == fixture["expected_fire_order"], order
    # Higher salience must strictly precede lower salience.
    last_high = max(i for i, r in enumerate(order) if r == "big-vip-order")
    first_low = min(i for i, r in enumerate(order) if r == "any-vip-order")
    assert last_high < first_low

    first = result.fired[0]
    exp = fixture["expected_first_fire_sources"]
    assert first.rule == exp["rule"]
    assert [list(k) for k in first.fact_keys] == exp["fact_keys"]
    log.info("first activation sources: rule=%s sources=%s bindings=%s",
             first.rule, [list(k) for k in first.fact_keys], first.bindings)


def test_multi_join_hand_expectations_reference_and_exclusions():
    fixture = load_fixture("multi_join.json")
    engine, _ = build_engine_from_fixture(fixture)

    got = normalize_engine_matches(engine.matches())
    expected = {
        (m["rule"],
         tuple(tuple(k) for k in m["fact_keys"]),
         tuple(sorted(m["bindings"].items())))
        for m in fixture["expected_matches"]
    }
    assert got == expected, sorted(got ^ expected)

    assert_match_sets_equal(
        engine.matches(), _reference_set_for(fixture, engine),
        context="multi_join")

    for excluded in fixture["excluded_combos"]:
        forbidden = (
            excluded["rule"],
            tuple(tuple(k) for k in excluded["fact_keys"]),
        )
        assert not any((r, keys) == forbidden for r, keys, _ in got), (
            f"excluded combo matched despite: {excluded['reason']}")
    log.info("multi_join: %d matches, %d exclusion reasons verified",
             len(got), len(fixture["excluded_combos"]))


def test_alpha_and_join_sharing_for_identical_condition_prefix():
    fixture = load_fixture("shared_condition.json")
    engine, _ = build_engine_from_fixture(fixture)
    net = engine.network
    # any-rule:   order -> customer(beq)
    # big-rule:   order(gtest ?amount>1000, pushed down to join 1 because
    #             ?amount is already bound) -> customer(beq)
    # => 4 join nodes, but only TWO alpha memories (alpha sharing), and
    #    each alpha memory feeds both rule branches.
    assert len(net.join_index) == 4
    assert len(net.productions) == 2
    assert len(net.alpha.memories) == 2
    by_kind = {m.kind: m for m in net.alpha.memories.values()}
    assert len(by_kind["order"].successors) == 2
    assert len(by_kind["customer"].successors) == 2
    # The early-filter join must carry the comparison test and shrink input.
    gtest_joins = [j for j in net.join_index.values()
                   if any(t[0] == "gtest" for t in j.tests)]
    assert len(gtest_joins) == 1
    log.info("sharing verified: %d join nodes over %d shared alpha memories; "
             "each alpha memory feeds %d rule branches",
             len(net.join_index), len(net.alpha.memories), 2)
