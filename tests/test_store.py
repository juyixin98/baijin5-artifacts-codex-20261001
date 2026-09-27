"""Evidence store: persistence, correlation and explicit statuses."""

from __future__ import annotations

import logging

from rete import Engine, __version__
from rete.store import EvidenceStore

log = logging.getLogger("tests.store")


def _engine_with_store(tmp_path):
    db = tmp_path / "evidence.db"
    store = EvidenceStore(str(db))
    engine = Engine(store=store)
    return engine, store, db


def test_run_evidence_is_persisted_and_correlated(tmp_path):
    engine, store, db = _engine_with_store(tmp_path)
    engine.add_rule({"name": "r", "salience": 3,
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [{"op": "assert", "kind": "g",
                                  "fields": ["?x"]},
                                 {"op": "emit", "tag": "t",
                                  "fields": ["?x"]}]})
    engine.insert("f", ("v1",))
    result = engine.run(10)

    run_row = store.get_run(result.run_id)
    assert run_row is not None
    assert run_row["status"] == "quiescent"
    assert run_row["cycles"] == result.cycles
    assert run_row["engine_version"] == __version__
    assert run_row["session_id"] == engine.session_id

    activations = store.list_activations(result.run_id)
    assert len(activations) == 1
    act = activations[0]
    assert act["rule"] == "r" and act["salience"] == 3
    assert act["fact_keys"] == [["f", "v1"]]
    assert act["bindings"] == {"?x": "v1"}
    assert {r["op"]: r["outcome"] for r in act["action_results"]} == {
        "assert": "asserted", "emit": "emitted"}

    events = store.list_fact_events(engine.session_id, result.run_id)
    ops = [(e["op"], e["kind"], tuple(e["fields"]), e["refcount"])
           for e in events]
    assert ("insert", "g", ("v1",), 1) in ops
    # Every fact event carries correlation data.
    for e in store.list_fact_events(engine.session_id):
        assert e["session_id"] == engine.session_id
        assert e["wme_id"] >= 1
    log.info("evidence correlated by session=%s run=%s; %d activations, "
             "%d fact events", engine.session_id, result.run_id,
             len(activations), len(events))
    store.close()

    # Reopen the file: durability, evidence survives the connection.
    reopened = EvidenceStore(str(db))
    assert reopened.get_run(result.run_id)["status"] == "quiescent"
    assert len(reopened.list_activations(result.run_id)) == 1
    log.info("evidence durable across connection reopen: %s", db)
    reopened.close()


def test_cycle_limit_status_persisted_not_quiescent(tmp_path):
    engine, store, _ = _engine_with_store(tmp_path)
    engine.add_rule({"name": "flap",
                     "conditions": [{"kind": "f", "fields": ["?x"]}],
                     "actions": [
                         {"op": "retract", "kind": "f", "fields": ["?x"]},
                         {"op": "assert", "kind": "f", "fields": ["?x"]}]})
    engine.insert("f", (1,))
    result = engine.run(1)
    assert result.status == "cycle_limit_reached"
    row = store.get_run(result.run_id)
    assert row["status"] == "cycle_limit_reached"
    assert row["cycles"] == 1
    log.info("limit-stop persisted with explicit non-success status")
    store.close()


def test_rule_definitions_persisted(tmp_path):
    store = EvidenceStore(str(tmp_path / "e.db"))
    body = {"name": "r", "conditions": [{"kind": "f", "fields": ["?x"]}],
            "actions": [], "salience": 0, "tests": []}
    sid = "sess-x"
    store.record_rule(sid, "r", body, "2026-09-28T00:00:00+00:00")
    store.close()
    reopened = EvidenceStore(str(tmp_path / "e.db"))
    import sqlite3
    row = reopened._conn.execute(
        "SELECT body FROM rule_defs WHERE session_id=? AND rule_name=?",
        (sid, "r")).fetchone()
    assert row is not None
    import json
    assert json.loads(row[0])["name"] == "r"
    reopened.close()
    log.info("rule definition persisted")
