# Structured Rete Production-Rule Engine

A fact-based, reviewable implementation of a **Rete** match network in
Python — not a single-file sketch: separate rule language, reasoning
kernel (alpha + beta network), evidence storage (SQLite) and a FastAPI
query interface, with an independent brute-force oracle and a thorough
test layer.

## What it implements

- **Alpha network** with constant-test filtering and an explicit per-memory
  index `(position, value) -> {wme_id}`; alpha memories are **shared**
  across rules keyed by `(kind, arity, constant-tests)`.
- **Beta network** with tokens, join nodes and beta memories. Each beta
  memory holds, per child join, an index `join-variables -> {key ->
  {token_id}}`, so left/right activation looks candidates up instead of
  scanning. Join nodes with the same parent/alpha/tests are shared.
- **Insert / retract** with documented duplicate semantics: a fact is its
  content key; identical inserts are reference-counted and match **once**;
  retraction removes the WME — and **every** dependent token and agenda
  activation — only when the count reaches zero. WME ids are monotonic and
  never reused.
- **Activation identity** = `(rule name, tuple of fact ids in condition
  order)`: bound to the exact fact combination.
- **Conflict resolution**: higher `salience`, then rule name, then the
  stable fact-id tuple.
- **Bounded execution**: actions never run in an unbounded loop. `run(n)`
  fires at most `n` activations and reports an explicit
  `cycle_limit_reached` status (never silently "ok") when work remains.
- **Actions**: `assert`, `retract`, `emit`; rules may trigger other rules.
- **Evidence store**: every run, fact event and firing (with source fact
  provenance and action outcomes) is persisted to SQLite and correlated by
  `session_id` / `run_id`.

## Layout

```
rete/                 reasoning kernel
  pattern.py          rule language (conditions, variables, tests, actions), parser
  facts.py            working memory, WME identity + reference counting
  alpha.py            alpha network, alpha memories, (position,value) index
  beta.py             tokens, beta memories + join-var indexes, joins, terminals
  network.py          rule -> network compiler with alpha/beta sharing + replay
  agenda.py           conflict set ordering, activation identity
  engine.py           Engine: insert/retract/match/run, bounded firing, logging
  store.py            SQLite evidence store
  config.py           configuration layer (env-mapped)
  errors.py           named, typed failure categories
rete_api/             FastAPI query/control interface
  app.py              routes, explicit error categories
  service.py          per-session engine management
  schemas.py          request/response models
tests/
  reference.py        INDEPENDENT brute-force matcher (imports nothing from rete)
  fixtures/*.json     hand-authored scenarios with hand-derived expectations
  test_*.py           core matches, differential, retract, self-join,
                      chaining, semantics, indexes, evidence, HTTP API
```

## Requirements

| Component | Version |
|---|---|
| Python | 3.12 |
| fastapi | 0.141.1 |
| uvicorn | 0.54.0 |
| pydantic | 2.13.5 |
| pytest | 9.1.1 |
| httpx | 0.28.1 (TestClient) |
| SQLite | stdlib (`sqlite3`) |

Install (a virtualenv is recommended):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

All data is local/synthetic. No external accounts or services.

## Rule language

```json
{
  "name": "vip-big-order",
  "salience": 10,
  "conditions": [
    {"kind": "customer", "fields": ["?cid", "vip"]},
    {"kind": "order",    "fields": ["?oid", "?cid", "?amount"]}
  ],
  "tests": [[">", "?amount", 1000]],
  "actions": [
    {"op": "assert",  "kind": "flag", "fields": ["?oid", "high-value"]},
    {"op": "emit",    "tag": "flagged", "fields": ["?oid"]}
  ]
}
```

- Variables start with `?`; other fields are JSON-scalar constants.
- `tests`: `[op, a, b]`, operators `== != < <= > >=` (aliases `eq ne ...`).
- `actions`: `assert` / `retract` with `kind`+`fields`, or `emit` with
  `tag`+`fields`.
- Duplicate fact content is one fact (see duplicate semantics above).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `RETE_DB_PATH` | `rete_evidence.db` | SQLite evidence file (`:memory:` allowed) |
| `RETE_LOG_LEVEL` | `INFO` | log level |
| `RETE_DEFAULT_MAX_CYCLES` | `100` | default firing bound |
| `RETE_MAX_CYCLES_LIMIT` | `100000` | hard ceiling; unbounded runs are rejected |

## Running the service

```bash
RETE_DB_PATH=rete_evidence.db uvicorn rete_api.app:app --host 127.0.0.1 --port 8000
```

### Request samples

```bash
# 1. create an isolated session
curl -s -X POST localhost:8000/sessions -H 'Content-Type: application/json' -d '{}'
# -> {"session_id":"<sid>", ...}

SID=<sid>

# 2. add a rule
curl -s -X POST localhost:8000/sessions/$SID/rules -H 'Content-Type: application/json' -d '{
  "name":"vip-big-order","salience":10,
  "conditions":[
    {"kind":"customer","fields":["?cid","vip"]},
    {"kind":"order","fields":["?oid","?cid","?amount"]}],
  "tests":[[">","?amount",1000]],
  "actions":[{"op":"emit","tag":"flag","fields":["?oid","?amount"]}]}'

# 3. insert facts
curl -s -X POST localhost:8000/sessions/$SID/facts -H 'Content-Type: application/json' \
  -d '{"kind":"customer","fields":["c1","vip"]}'
curl -s -X POST localhost:8000/sessions/$SID/facts -H 'Content-Type: application/json' \
  -d '{"kind":"order","fields":["o1","c1",5000]}'

# 4. inspect matches (each carries fact_ids / fact_keys provenance)
curl -s localhost:8000/sessions/$SID/matches

# 5. run, bounded; status is quiescent or cycle_limit_reached
curl -s -X POST localhost:8000/sessions/$SID/run -H 'Content-Type: application/json' \
  -d '{"max_cycles":100}'

# 6. retract (dependent matches/activations are deleted)
curl -s -X DELETE localhost:8000/sessions/$SID/facts -H 'Content-Type: application/json' \
  -d '{"kind":"order","fields":["o1","c1",5000]}'

# 7. evidence for a run (run_id from step 5)
curl -s localhost:8000/sessions/$SID/runs
curl -s localhost:8000/sessions/$SID/runs/<run_id>/activations
```

Errors always carry an explicit category, e.g.
`{"detail":{"error":"RuleError","category":"rule_error","detail":"..."}}`;
categories include `rule_error` (422), `duplicate_rule` (409),
`rule_not_found` / `fact_not_found` / `session_not_found` (404),
`cycle_limit_invalid` (422), `internal_error` (500).

## Tests

```bash
python3 -m pytest
```

What is verified (tests assert **specific** results and failure classes,
not "endpoint callable"):

- hand-authored fixtures vs hand-derived expectations **and** vs the
  independent oracle (`shared_condition`, `multi_join`);
- seeded random **differential** runs: after every insert/retract the
  incremental network equals the brute-force matcher; plus rules added
  *after* facts (replay);
- retraction deletes all dependent matches/agenda entries, reference
  counting, retract-then-reinsert recreation;
- self-joins (one fact in several condition positions): exactly-once
  match/retract;
- rules triggering rules: exact firing sequence, source provenance for
  every activation, re-entry after quiescence;
- agenda ordering (salience, name, stable fact-id tiebreak);
- alpha and beta **index structure** and indexed activation, including
  index cleanup on retraction;
- malformed rules / unknown facts / invalid cycle bounds raise named
  errors; the cycle limit is reported as `cycle_limit_reached`;
- evidence is persisted, correlated and survives connection reopen;
- full HTTP flow and explicit error statuses/categories.

Structured logs for each test run are written to `tests/logs/`; each line
carries the test run id and pytest node id (input identity), and engine
lines carry `session`/`run` ids, version, cycle progress and decisions.

## Reproduce from a clean checkout

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest
RETE_DB_PATH=/tmp/rete.db uvicorn rete_api.app:app --port 8000
# then run the request samples above
```
