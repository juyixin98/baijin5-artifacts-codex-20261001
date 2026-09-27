"""Rule-language parser validation: every malformed input is a RuleError."""

from __future__ import annotations

import logging

import pytest

from rete import RuleError, rule_from_dict

log = logging.getLogger("tests.pattern")


def test_rule_parser_accepts_a_well_formed_rule():
    rule = rule_from_dict({
        "name": "ok", "salience": 3,
        "conditions": [{"kind": "a", "fields": ["?x", 1]},
                       {"kind": "b", "fields": ["?y", "?x"]}],
        "tests": [["<", "?x", "?y"]],
        "actions": [{"op": "assert", "kind": "c", "fields": ["?x"]},
                    {"op": "emit", "tag": "seen", "fields": ["?x"]}]})
    assert rule.name == "ok" and rule.salience == 3
    assert rule.conditions[0].constant_tests == ((1, 1),)
    assert rule.conditions[0].variables == ("?x",)
    log.info("well-formed rule parsed: %d conditions, %d actions",
             len(rule.conditions), len(rule.actions))


@pytest.mark.parametrize("body,label", [
    ({"conditions": []}, "missing name key"),
    ({"name": "x"}, "missing conditions key"),
    ("not-an-object", "rule not an object"),
    ({"name": "x", "conditions": "not-a-list", "actions": []},
     "conditions not iterable of dicts"),
    ({"name": "x", "conditions": [{"fields": []}], "actions": []},
     "condition missing kind"),
    ({"name": "x", "conditions": [{"kind": "a", "fields": [["nested"]]}]},
     "non-scalar field"),
    ({"name": "x", "conditions": [{"kind": "", "fields": []}], "actions": []},
     "empty kind"),
    ({"name": "x", "salience": "high",
      "conditions": [{"kind": "a"}]}, "non-int salience"),
])
def test_rule_parser_rejects_bad_inputs_with_rule_error(body, label):
    with pytest.raises(RuleError):
        rule_from_dict(body)
    log.info("rejected %s", label)


def test_action_validation():
    with pytest.raises(RuleError):
        rule_from_dict({"name": "x",
                        "conditions": [{"kind": "a", "fields": []}],
                        "actions": [{"op": "emit"}]})  # emit needs tag
    with pytest.raises(RuleError):
        rule_from_dict({"name": "x",
                        "conditions": [{"kind": "a", "fields": []}],
                        "actions": [{"op": "assert"}]})  # assert needs kind
    with pytest.raises(RuleError):
        rule_from_dict({"name": "x",
                        "conditions": [{"kind": "a", "fields": []}],
                        "actions": [{"op": "wat"}]})  # unknown op


def test_test_must_have_three_parts():
    with pytest.raises(RuleError):
        rule_from_dict({"name": "x",
                        "conditions": [{"kind": "a", "fields": ["?x"]}],
                        "tests": [["?", "?x"]]})
