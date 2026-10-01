# Verification results

These are results captured from real local runs (debug build, Rust 1.98.1,
Linux x86_64). They are reproducible with the commands shown; request ids and
timestamps naturally differ between runs, but **decisions, row counts,
categories and counters are deterministic** for the committed fixtures.

All data is the committed synthetic data under [`fixtures/`](../fixtures); no
external service or account is used.

## 1. Automated test run

```text
$ cargo test
src (unit):         22 passed
tests/triangle:      4 passed
tests/skew:          3 passed
tests/multitable:    4 passed
tests/api:           8 passed
tests/differential:  3 passed
-------------------------
total:              44 passed, 0 failed
```

`cargo clippy --all-targets -- -D warnings` and `cargo fmt --check` are clean.

## 2. Offline validation walkthrough

`cargo run --example run_validation` (abridged, real numbers):

### Triangle over K5 — expected 10 triangles

```text
decision: Accepted, stop=complete
lftj : emitted=10 intermediate_tuples=0 seeks=10 comparisons=22 nexts=40 opens=22
naive: matches=true probes=20 intermediate_tuples=30 emitted=10
```

The ten rows are exactly the 3-element subsets `{0,1,2,3,4}` with `a<b<c`,
i.e. `C(5,3)=10`, matching the hand-derived answer.

### Highly skewed, sparse intersection — expected 5 rows

R and S agree on a 200×200 hub (`b=1`); T intersects only five pairs.

```text
rows: [7,1,7] [42,1,42] [100,1,100] [200,1,200] [1002,2,9002]
lftj : emitted=5 intermediate_tuples=0 seeks=108 comparisons=260 nexts=222 opens=214
       (total trie accesses = 804)
naive: matches=true probes=40,400 intermediate_tuples=40,405 emitted=5
```

The deliberately naive request-order binary plan materialises **40,405**
tuples (300 root rows + 40,100 `R⋈S` prefixes + 5 final rows); 40,000 of the
prefixes are hub combinations T rejects. Leapfrog materialises **zero**
intermediate tuples and performs 804 trie accesses total — about a **50×** gap
on this small fixture, and the gap widens with hub fan-out.

## 3. Large triangle (K200) — no intermediate product at scale

`tests/triangle.rs::triangle_k200_engine_materialises_no_intermediate_at_all`:

| Implementation | output rows | intermediate tuples materialised |
|---|---:|---:|
| independent naive binary plan | 1,313,400 (`C(200,3)`) | 2,646,700 |
| Leapfrog Triejoin | 1,313,400 (`C(200,3)`) | **0** |

The 2,646,700 figure decomposes as 19,900 root edge rows + 1,313,400
length-2-path prefixes + 1,313,400 final rows. Both implementations return the
identical multiset; only Leapfrog avoids the intermediate `R⋈S` product.

## 4. HTTP service — normal paths (captured live)

Server started with `cargo run --bin lftj-server`; embedded fixtures loaded:

```text
GET /health -> 200 {"status":"ok","max_relations":6,"default_limit":1024,...}
GET /relations -> {"relations":["skew_r","skew_s","skew_t",
                                  "triangle_r","triangle_s","triangle_t"]}

POST /query  {"relation_names":["triangle_r","triangle_s","triangle_t"],
              "compare_naive":true}
  -> 200 decision=accepted row_count=10 stop_reason=complete
        stats.intermediate_tuples_materialized=0
        naive.matches=true naive.naive_emitted_rows=10

POST /query  {"relation_names":["skew_r","skew_s","skew_t"],"compare_naive":true}
  -> 200 decision=accepted row_count=5
        rows [[7,1,7],[42,1,42],[100,1,100],[200,1,200],[1002,2,9002]]

POST /query/arrow (same triangle request)
  -> 200 content-type: application/vnd.apache.arrow.stream
     x-decision=accepted x-row-count=10 x-intermediate-tuples=0
     body = decodable Arrow IPC stream, 904 bytes
```

The IPC stream was decoded back with `arrow2::io::ipc` in
`tests/api.rs::arrow_endpoint_returns_decodable_ipc_stream` and asserted to
contain three fields and ten rows.

## 5. Pagination / resumable interruption (captured live)

`fixtures/requests/page1.json` (12-result join) with `limit=5`:

```text
page 1: 5 rows stop=output_limit
page 2: 5 rows stop=output_limit
page 3: 2 rows stop=complete
total 12, 12 unique -> no rows lost or duplicated
```

Cursors are opaque, single-use, TTL-bounded and fingerprinted to the exact
query (a cursor from another query, an unknown token, or a replayed token all
return `invalid_cursor`).

## 6. Access-budget interruption -> undecidable -> resume (captured live)

`fixtures/requests/budget.json` (`access_budget=4`, 20-result join):

```text
first : HTTP 202 decision=undecidable stop=budget_exhausted rows=1 next_cursor=<token>
resume: HTTP 200 decision=accepted   stop=complete         rows=19
sum    : 1 + 19 = 20 (exact complement; nothing lost or duplicated)
```

This is the "cannot decide yet" category: completeness was not provable when
the access budget was spent, but the partial result is resumable.

## 7. Failure categories (captured live)

Every abnormal input is rejected with a stable code and a correlated request
id; the same id appears in the structured server log.

| Input | HTTP | `error.code` |
|---|---:|---|
| `{bad` (not JSON) | 400 | `malformed_json` |
| two relations with no shared columns | 400 | `disjoint_schema` |
| shared `a` typed int vs string | 422 | `type_mismatch` |
| NULL in key `a`, default policy | 400 | `null_key` |
| `relation_names:["nope"]` | 422 | `unknown_relation` |
| 7 relations (> max 6) | 422 | `unsupported_shape` |
| unknown/replayed/foreign cursor | 400 | `invalid_cursor` |

NULL policy contrast (captured):

```text
default reject   -> 400 null_key
sql_match_never  -> 200, the NULL-key row excluded; only the matching row [2] returned
```

## 8. Diagnostics and redaction

- Every response carries `request_id` (also the `x-request-id` header); the
  server emits one JSON log line per decision with the key state
  (relation sizes, join attributes, emitted/skipped counts, trie counters,
  stop reason, error category).
- Cell values are never written to logs or error bodies. The redaction helper
  renders a value as `<int>`, `<bool>` or `<string:len=N>` only
  (`diagnostics::tests::redaction_never_emits_content`).
