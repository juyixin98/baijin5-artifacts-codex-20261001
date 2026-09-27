"""Retraction tests: dependent matches/activations deleted, refcount-aware,
re-insert recreates identical matches. Driven by an explicit script fixture
with asserted states after every step."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rete import Engine, FactNotFoundError
from reference import reference_matches

log = logging.getLogger("tests.retract")

FIXTURE = Path(__file__).parent / "fixtures" / "retract_reinsert.json"


def _fact_map(engine) -> dict:
    return {(w.kind, *w.fields): w for w in engine.facts.distinct_facts()}


def _match_keys(engine, rule: str) -> list:
    return sorted(
        tuple(tuple(k) for k in m["fact_keys"])
        for m in engine.matches(rule))


def test_retract_reinsert_script():
    fixture = json.loads(FIXTURE.read_text())
    engine = Engine()
    for rule in fixture["rules"]:
        engine.add_rule(rule)

    for step_no, step in enumerate(fixture["script"], 1):
        op = step["op"]
        if op in ("insert", "retract"):
            if op == "insert":
                engine.insert(step["kind"], tuple(step["fields"]))
            else:
                engine.retract(step["kind"], tuple(step["fields"]))
            log.info("step %d %s %s %s note=%s", step_no, op,
                     step["kind"], step["fields"], step.get("note", ""))
        elif op == "assert_matches":
            for rule, expected_combos in step["rules"].items():
                got = _match_keys(engine, rule)
                want = sorted(tuple(tuple(k) for k in combo)
                              for combo in expected_combos)
                assert got == want, (
                    f"step {step_no} rule {rule}: got={got} want={want}")
            # Cross-check the whole state with the independent oracle.
            facts = [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
            oracle = reference_matches(fixture["rules"], facts)
            observed = {
                (m["rule"], tuple(tuple(k) for k in m["fact_keys"]))
                for m in engine.matches()
            }
            oracle_pairs = {(r, c) for r, c, _ in oracle}
            assert observed == oracle_pairs, (
                f"step {step_no}: {observed ^ oracle_pairs}")
        elif op == "assert_facts":
            got = sorted(_fact_map(engine))
            want = sorted(tuple(k) for k in step["facts"])
            assert got == want, f"step {step_no}: {got} != {want}"
        else:
            raise AssertionError(f"unknown script op {op!r}")

    log.info("retract/reinsert script: all %d steps verified", len(fixture["script"]))


def test_retract_removes_agenda_activations_for_dependent_matches():
    engine = Engine()
    engine.add_rule({
        "name": "r",
        "conditions": [{"kind": "a", "fields": ["?x"]},
                       {"kind": "b", "fields": ["?x"]}],
        "actions": [{"op": "emit", "tag": "t", "fields": ["?x"]}],
    })
    engine.insert("a", ("k",))
    engine.insert("b", ("k",))
    assert len(engine.agenda) == 1
    engine.retract("a", ("k",))
    assert engine.matches("r") == []
    assert len(engine.agenda) == 0, "activation survived retraction"
    log.info("agenda cleared after retracting one side of a join")


def test_retract_missing_fact_is_named_error_not_silent_success():
    engine = Engine()
    engine.insert("a", (1,))
    try:
        engine.retract("a", (2,))
    except FactNotFoundError as e:
        assert e.kind == "a" and e.fields == (2,)
        log.info("missing-fact retract raised FactNotFoundError as required")
    else:
        raise AssertionError("retracting a non-existent fact must raise")


def test_fact_identity_stable_but_ids_never_reused():
    engine = Engine()
    w1, created1 = engine.insert("a", ("k",))
    assert created1
    engine.retract("a", ("k",))
    w2, created2 = engine.insert("a", ("k",))
    assert created2
    assert w2.id != w1.id, "fact ids must never be reused"
    log.info("wme id not reused: %d -> %d after full remove", w1.id, w2.id)
