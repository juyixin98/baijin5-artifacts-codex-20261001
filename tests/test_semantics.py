"""Semantics: duplicate facts, agenda ordering, validation error categories."""

from __future__ import annotations

import logging

import pytest

from rete import (CycleLimitError, DuplicateRuleError, Engine, FactNotFoundError,
                  RuleError, rule_from_dict)

log = logging.getLogger("tests.semantics")


def test_duplicate_fact_is_refcounted_and_matches_once():
    engine = Engine()
    engine.add_rule({"name": "r",
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [{"op": "emit", "tag": "t", "fields": ["?x"]}]})
    w1, created1 = engine.insert("f", ("a",))
    w2, created2 = engine.insert("f", ("a",))
    assert created1 is True and created2 is False
    assert w1.id == w2.id and w1.count == 2
    assert len(engine.facts) == 1
    assert len(engine.matches("r")) == 1, "duplicate content must not duplicate matches"
    assert len(engine.agenda) == 1
    engine.retract("f", ("a",))
    assert len(engine.facts) == 1 and len(engine.matches("r")) == 1
    engine.retract("f", ("a",))
    assert len(engine.facts) == 0 and engine.matches("r") == []
    log.info("duplicate semantics: one content -> one fact/match, refcounted")


def test_activation_identity_is_rule_plus_fact_tuple():
    engine = Engine()
    engine.add_rule({"name": "r", "conditions": [
        {"kind": "a", "fields": ["?x"]}, {"kind": "b", "fields": ["?x"]}],
        "actions": []})
    wa, _ = engine.insert("a", ("k",))
    wb, _ = engine.insert("b", ("k",))
    acts = engine.agenda.peek_order()
    assert len(acts) == 1
    assert acts[0].identity == ("r", (wa.id, wb.id))
    log.info("activation identity = (rule, fact tuple) = %r", acts[0].identity)


def test_agenda_order_salience_name_then_fact_ids():
    engine = Engine()
    for name, sal in [("zeta", 0), ("alpha", 0), ("mid", 5), ("top", 9)]:
        engine.add_rule({"name": name, "salience": sal,
                         "conditions": [{"kind": name, "fields": ["?x"]}],
                         "actions": [{"op": "emit", "tag": name}]})
    engine.insert("zeta", (1,))
    engine.insert("alpha", (1,))
    engine.insert("mid", (1,))
    engine.insert("top", (1,))
    result = engine.run(10)
    assert [f.rule for f in result.fired] == ["top", "mid", "alpha", "zeta"]
    log.info("agenda ordering verified: salience, then name, then fact ids")


def test_agenda_stable_fact_id_tiebreak_inside_one_rule():
    engine = Engine()
    engine.add_rule({"name": "r",
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [{"op": "emit", "tag": "t", "fields": ["?x"]}]})
    for v in ("p", "q", "r"):
        engine.insert("f", (v,))
    result = engine.run(10)
    assert [f.bindings["?x"] for f in result.fired] == ["p", "q", "r"]
    log.info("stable fact-id tiebreak verified")


@pytest.mark.parametrize("rule,bad_part", [
    ({"name": "no-conds", "conditions": []}, "empty conditions"),
    ({"name": "unbound-test",
      "conditions": [{"kind": "f", "fields": ["?x"]}],
      "tests": [[">", "?y", 1]]}, "unbound test variable"),
    ({"name": "unbound-action",
      "conditions": [{"kind": "f", "fields": ["?x"]}],
      "actions": [{"op": "assert", "kind": "g", "fields": ["?z"]}]},
     "unbound action variable"),
    ({"name": "bad-op",
      "conditions": [{"kind": "f", "fields": ["?x"]}],
      "tests": [["??", "?x", 1]]}, "unknown operator"),
    ({"name": "bad-action",
      "conditions": [{"kind": "f", "fields": ["?x"]}],
      "actions": [{"op": "explode"}]}, "unknown action"),
])
def test_malformed_rules_raise_named_rule_error(rule, bad_part):
    with pytest.raises(RuleError):
        rule_from_dict(rule)
    log.info("rejected malformed rule (%s): %s", bad_part, rule["name"])


def test_duplicate_rule_name_is_named_conflict():
    engine = Engine()
    body = {"name": "r", "conditions": [{"kind": "f", "fields": []}]}
    engine.add_rule(body)
    with pytest.raises(DuplicateRuleError):
        engine.add_rule(body)
    log.info("duplicate rule name rejected with DuplicateRuleError")


def test_retract_never_silently_succeeds_for_unknown_fact():
    engine = Engine()
    with pytest.raises(FactNotFoundError) as exc:
        engine.retract("ghost", (1, 2))
    assert exc.value.kind == "ghost"
    log.info("unknown retract -> FactNotFoundError (no fake success)")


def test_run_requires_positive_bounded_cycles():
    engine = Engine()
    engine.add_rule({"name": "r", "conditions": [{"kind": "f", "fields": []}],
                     "actions": []})
    engine.insert("f", ())
    for bad in (0, -5):
        with pytest.raises(CycleLimitError):
            engine.run(bad)
    with pytest.raises(CycleLimitError):
        engine.run(10 ** 9)
    log.info("unbounded/over-limit runs rejected with CycleLimitError")


def test_cycle_limit_status_is_explicit_not_quiescent():
    engine = Engine()
    engine.add_rule({"name": "flap",
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [
                         {"op": "retract", "kind": "f", "fields": ["?x"]},
                         {"op": "assert", "kind": "f", "fields": ["?x"]}]})
    engine.insert("f", (1,))
    result = engine.run(2)
    assert result.status == "cycle_limit_reached"
    assert result.cycles == 2
    assert result.agenda_remaining >= 1
    log.info("limit stop reported as cycle_limit_reached, never as success")


def test_retract_action_on_missing_fact_is_recorded_not_faked():
    engine = Engine()
    engine.add_rule({"name": "cleanup",
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [
                         # retract the triggering fact itself (succeeds)...
                         {"op": "retract", "kind": "f", "fields": ["?x"]},
                         # ...then attempt a fact that does not exist. The
                         # outcome must be explicit, not a silent success.
                         {"op": "retract", "kind": "ghost", "fields": ["?x"]}]})
    engine.insert("f", ("v",))
    result = engine.run(1)
    assert result.status == "quiescent"
    outcomes = [(r["op"], r["outcome"]) for r in result.fired[0].action_results]
    assert outcomes == [("retract", "retracted"),
                        ("retract", "fact_not_found")]
    log.info("missing-fact retract action recorded fact_not_found explicitly")
