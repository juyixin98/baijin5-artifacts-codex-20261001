"""Explicit verification of alpha and beta memory indexing.

These tests assert not just results but the indexed *structure* the engine
uses to avoid scans:

  * alpha memory: (position, value) -> {wme_id} buckets, one memory per
    distinct (kind, arity, constant-tests) key shared across rules;
  * beta memory: join-variable tuple -> {key tuple -> {token_id}} buckets
    attached per child join node.
"""

from __future__ import annotations

import logging

from rete import Engine

log = logging.getLogger("tests.indexes")


def _engine():
    engine = Engine()
    engine.add_rule({
        "name": "r",
        "conditions": [
            {"kind": "customer", "fields": ["?cid", "vip"]},
            {"kind": "order", "fields": ["?oid", "?cid", "?amt"]}],
        "actions": []})
    return engine


def test_alpha_memory_index_buckets_point_to_exact_wmes():
    engine = _engine()
    wc1, _ = engine.insert("customer", ("c1", "vip"))
    wc2, _ = engine.insert("customer", ("c2", "vip"))
    engine.insert("customer", ("c3", "standard"))   # excluded by constant
    engine.insert("order", ("o1", "c1", 10))        # wrong arity kind? same kind, arity 3 -> separate memory

    # Exactly two customer facts pass the constant test field[1]=='vip'.
    vip_mem = next(m for m in engine.network.alpha.memories.values()
                   if m.kind == "customer" and m.const_tests == ((1, "vip"),))
    assert set(vip_mem.items) == {wc1.id, wc2.id}

    # The index must answer a lookup without scanning:
    assert vip_mem.candidates(0, "c1") == {wc1.id}
    assert vip_mem.candidates(0, "c2") == {wc2.id}
    assert vip_mem.candidates(1, "vip") == {wc1.id, wc2.id}
    assert vip_mem.candidates(0, "c3") == set()
    log.info("alpha index: position/value buckets verified (%d items)",
             len(vip_mem))


def test_alpha_memories_are_keyed_by_kind_arity_and_constants():
    engine = Engine()
    engine.add_rule({"name": "r1",
                     "conditions": [{"kind": "f", "fields": ["?a", "x"]}],
                     "actions": []})
    engine.add_rule({"name": "r2",
                     "conditions": [{"kind": "f", "fields": ["?a", "y"]}],
                     "actions": []})
    engine.add_rule({"name": "r3",
                     "conditions": [{"kind": "f", "fields": ["?a", "x", "?b"]}],
                     "actions": []})
    keys = set(engine.network.alpha.memories)
    assert ("f", 2, ((1, "x"),)) in keys
    assert ("f", 2, ((1, "y"),)) in keys
    assert ("f", 3, ((1, "x"),)) in keys
    log.info("alpha memory keys: kind + arity + constant tests = %s",
             sorted(keys))


def test_beta_memory_indexes_join_variables_to_token_ids():
    engine = _engine()
    wc1, _ = engine.insert("customer", ("c1", "vip"))
    engine.insert("customer", ("c2", "vip"))
    engine.insert("order", ("o1", "c1", 10))
    engine.insert("order", ("o2", "c1", 20))
    engine.insert("order", ("o3", "c2", 30))

    # The second join node indexes its parent (post-customer) beta memory
    # on the join variable ?cid.
    joins = list(engine.network.join_index.values())
    order_join = next(j for j in joins if j.alpha.kind == "order")
    assert order_join.join_vars == ("?cid",)
    parent_mem = order_join.parent
    idx = parent_mem.indexes[("?cid",)]

    # Two customers -> exactly two buckets, holding the partial-match tokens.
    assert set(idx) == {("c1",), ("c2",)}
    token_ids_c1 = idx[("c1",)]
    assert token_ids_c1 and token_ids_c1 <= set(parent_mem.tokens)
    hits = parent_mem.lookup(("?cid",), ("c1",))
    assert all(t.bindings["?cid"] == "c1" for t in hits)
    assert parent_mem.lookup(("?cid",), ("zzz",)) == []
    log.info("beta index: ?cid buckets c1=%d tokens, c2=%d tokens",
             len(idx[("c1",)]), len(idx[("c2",)]))

    # Right activation of a new order hits the index, never scanning tokens:
    before = {t.id for t in order_join.output.tokens.values()}
    new_order, created = engine.insert("order", ("o4", "c1", 40))
    assert created
    after = {t.id for t in order_join.output.tokens.values()}
    new_tokens = after - before
    assert len(new_tokens) == 1
    tok = order_join.output.tokens[next(iter(new_tokens))]
    assert tok.bindings == {"?cid": "c1", "?oid": "o4", "?amt": 40}
    # Condition order is customer, order: the new order is the second WME,
    # joined to the already-existing customer c1 via the beta index.
    assert tok.wme_ids == (wc1.id, new_order.id)
    log.info("indexed right activation produced exactly one new token")


def test_beta_index_entries_are_removed_on_retraction():
    engine = _engine()
    engine.insert("customer", ("c1", "vip"))
    joins = list(engine.network.join_index.values())
    order_join = next(j for j in joins if j.alpha.kind == "order")
    parent_mem = order_join.parent
    assert ("c1",) in parent_mem.indexes[("?cid",)]
    engine.retract("customer", ("c1", "vip"))
    assert ("c1",) not in parent_mem.indexes[("?cid",)]
    assert parent_mem.tokens == {}
    log.info("beta index buckets removed with the partial matches on retract")
