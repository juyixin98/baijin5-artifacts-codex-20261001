"""Rules triggering rules: exact firing sequence, provenance and chaining."""

from __future__ import annotations

import logging

from conftest import build_engine_from_fixture, load_fixture

log = logging.getLogger("tests.chaining")


def _fire_sequence(result):
    return [{"cycle": f.cycle, "rule": f.rule,
             "bindings": dict(f.bindings)} for f in result.fired]


def test_rule_triggers_rule_fire_sequence_and_sources():
    fixture = load_fixture("rule_triggers.json")
    engine, _ = build_engine_from_fixture(fixture)
    result = engine.run(fixture["run_max_cycles"])

    assert result.status == fixture["expected_status"], result.status
    seq = _fire_sequence(result)
    assert seq == fixture["expected_fire_sequence"], (
        [(s["cycle"], s["rule"], s["bindings"]) for s in seq])

    # Every activation must expose its source facts (provenance).
    for fired in result.fired:
        assert fired.fact_ids, "activation without source fact ids"
        assert len(fired.fact_keys) == len(fired.fact_ids)
        for key in fired.fact_keys:
            assert key[0] in ("task", "worker")

    emits = [{"tag": r["tag"], "fields": r["fields"]}
             for f in result.fired for r in f.action_results
             if r["op"] == "emit"]
    assert emits == fixture["expected_emits"], emits

    facts = sorted((w.kind, *w.fields) for w in engine.facts.distinct_facts())
    assert facts == sorted(tuple(k) for k in fixture["expected_final_facts"])
    assert len(engine.matches()) == fixture["expected_final_match_count"]
    log.info("chaining: %d activations fired in exact expected order",
             len(result.fired))


def test_new_fact_after_quiescence_re_enters_chain():
    fixture = load_fixture("rule_triggers.json")
    engine, _ = build_engine_from_fixture(fixture)
    first = engine.run(fixture["run_max_cycles"])
    assert first.status == "quiescent"

    phase = fixture["new_task_then_run"]
    engine.insert(phase["insert"]["kind"], tuple(phase["insert"]["fields"]))
    second = engine.run(fixture["run_max_cycles"])
    assert second.status == "quiescent"

    seq = [{"rule": f.rule, "bindings": dict(f.bindings)}
           for f in second.fired]
    assert seq == phase["expected_fire_sequence"], seq

    facts = sorted((w.kind, *w.fields) for w in engine.facts.distinct_facts())
    assert facts == sorted(tuple(k) for k in phase["expected_final_facts"])
    log.info("post-quiescence insert re-entered chain with %d activations",
             len(second.fired))
