"""Differential tests: incremental Rete network vs the brute-force oracle.

Scenarios are generated with a FIXED seed (logged for reproduction) and
re-checked after every insert/retract, so a divergence pinpoints the exact
operation that first desynchronised the two implementations.
"""

from __future__ import annotations

import logging
import random
from collections import Counter

from rete import Engine
from reference import reference_matches

log = logging.getLogger("tests.differential")

KINDS = ["alpha", "beta", "gamma"]
VALUES = ["v1", "v2", 1, 2, None, True]
VARS = ["?x", "?y", "?z"]
OPS = ["==", "!=", "<", "<=", ">", ">="]


def _rule_set(rng: random.Random, n_rules: int) -> list[dict]:
    rules = []
    for i in range(n_rules):
        n_conds = rng.randint(1, 3)
        conds = []
        used_vars: set = set()
        for _ in range(n_conds):
            kind = rng.choice(KINDS)
            fields = [
                rng.choice(VARS + list(VALUES))
                for _ in range(rng.randint(1, 3))
            ]
            conds.append({"kind": kind, "fields": fields})
            used_vars.update(v for v in fields if isinstance(v, str)
                             and v.startswith("?"))
        # Every variable in tests must be bound in some condition.
        tests = []
        bound = sorted(used_vars)
        if bound and rng.random() < 0.5:
            for _ in range(rng.randint(1, 2)):
                op = rng.choice(OPS)
                a = rng.choice(bound)
                b = rng.choice(bound + [v for v in VALUES
                                        if isinstance(v, (int, float))])
                tests.append([op, a, b])
        rules.append({
            "name": f"rule-{i}",
            "salience": rng.randint(0, 5),
            "conditions": conds,
            "tests": tests,
            "actions": [],
        })
    return rules


def _engine_set(engine: Engine, rules: list[dict]) -> set:
    facts = [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
    return _normalize(reference_matches(rules, facts))


def _normalize(ref: set) -> set:
    return set(ref)


def _engine_observed(engine: Engine) -> set:
    out = set()
    for m in engine.matches():
        out.add((
            m["rule"],
            tuple(tuple(k) for k in m["fact_keys"]),
            tuple(sorted(m["bindings"].items())),
        ))
    return out


def _run_scenario(seed: int, steps: int = 120):
    rng = random.Random(seed)
    rules = _rule_set(rng, n_rules=rng.randint(2, 5))
    engine = Engine()
    for r in rules:
        engine.add_rule(r)

    counts: Counter = Counter()

    def fact_tuples():
        return [tuple(k) for k, c in counts.items() for _ in range(min(c, 1))]

    for step in range(steps):
        insert = not counts or rng.random() < 0.6
        if insert:
            kind = rng.choice(KINDS)
            fields = tuple(rng.choice(VALUES) for _ in range(rng.randint(1, 3)))
            engine.insert(kind, fields)
            counts[(kind, *fields)] += 1
            op_desc = f"insert {kind}{list(fields)}"
        else:
            key = rng.choice(list(counts))
            kind, *fields = key
            engine.retract(kind, tuple(fields))
            counts[key] -= 1
            if counts[key] == 0:
                del counts[key]
            op_desc = f"retract {list(key)}"

        got = _engine_observed(engine)
        want = _engine_set(engine, rules)
        if got != want:
            log.error("divergence seed=%d step=%d op=%s", seed, step, op_desc)
            for m in sorted(want - got):
                log.error("MISSING %s", m)
            for e in sorted(got - want):
                log.error("EXTRA %s", e)
            assert got == want, (
                f"seed={seed} step={step} op={op_desc}; "
                f"missing={sorted(want - got)[:3]} extra={sorted(got - want)[:3]}")
    log.info("scenario seed=%d converged across %d steps, %d distinct facts, "
             "%d matches", seed, steps, len(counts), len(_engine_observed(engine)))
    return engine, rules


def test_differential_random_scenarios():
    seeds = [101, 202, 303, 404, 505, 606, 707, 808]
    for seed in seeds:
        _run_scenario(seed)


def test_differential_rule_added_after_facts_matches_oracle():
    seed = 909
    rng = random.Random(seed)
    rules = _rule_set(rng, n_rules=4)
    engine = Engine()
    # Insert a body of facts before ANY rule exists.
    seeded = []
    for _ in range(40):
        kind = rng.choice(KINDS)
        fields = tuple(rng.choice(VALUES) for _ in range(rng.randint(1, 3)))
        seeded.append((kind, fields))
        engine.insert(kind, fields)
    # Add rules one at a time; each must replay the existing facts correctly.
    for r in rules:
        engine.add_rule(r)
        got = _engine_observed(engine)
        want = _engine_set(engine, [x for x in rules
                                    if x["name"] <= r["name"]])
        assert got == want, (
            f"after adding {r['name']}: missing={sorted(want - got)[:3]} "
            f"extra={sorted(got - want)[:3]}")
    log.info("late-rule replay seed=%d OK; %d total matches", seed, len(got))
