# Leapfrog Triejoin Backend (Rust + Axum + Arrow2)

A backend for **three-table and restricted multi-table natural joins** evaluated
with the worst-case-optimal **Leapfrog Triejoin (LFJ)** algorithm. It builds
ordered tries per relation, intersects variable domains with binary-search
*leaps* (never pairwise scans), preserves **bag multiplicity**, enforces a
**restricted** join (rejecting Cartesian products), supports an explicit
**NULL policy**, and delivers **resumable, capped pages** of Arrow/JSON output.

All data and participants are **local and synthetic** — no external accounts or
real business data are needed.

## Why it does not explode

A naive left-deep engine materializes the running cross product after each
binary join, applying later relations only as filters. On the bundled skew
fixture `ab ⋈ ac ⋈ pick_b`, the first binary join is
`1001 × 1000 = 1,001,000` rows even though the selective `pick_b` reduces the
final answer to 1000. LFJ instead intersects the smallest domain at each
variable and seeks the others via binary search, so no pairwise intermediate is
ever allocated. `tests/skew_sparse_test.rs` asserts both the exact answer and
that the naive oracle's `max_intermediate >= 1,000,000` while LFJ stays sparse.

## Module layout (real responsibilities, not a single file)

```
src/
  domain/      Typed Datum, LogicalType, Multiplicity, NullPolicy
  batch.rs     Typed columnar batches + schema validation at the boundary
  trie/        Ordered tries, leaf/subtree multiplicity, leapfrog cursors + counters
  query/       Request model, validation, plan/trie-order compilation
  join/
    engine.rs  Generalized multi-relation LFJ (3-table triangle is the arity-3 case)
    cursor.rs  Opaque resumption tokens
    naive.rs   Independent naive full-enumeration oracle (tests + blow-up demo only)
  arrow_io.rs  Arrow2 arrays + IPC stream encoding
  resource.rs  Limits, fixture catalog, request ids, redacted diagnostics
  fixtures.rs  Local synthetic datasets
  api/         service pipeline (service.rs) and Axum HTTP boundary (http.rs)
tests/         Integration, differential, HTTP, and Arrow round-trip tests
fixtures/      Minimal inspectable JSON request fixtures
examples/      curl/service call script
docs/          Algorithm, API, and verification reports
```

## Build and test

Requires Rust 1.98+. Dependencies are pinned in `Cargo.lock`.

```bash
cargo build --release
cargo test                       # 35 tests, incl. randomized differential tests
cargo clippy --all-targets -- -D warnings
cargo fmt
```

## Run the server

```bash
cargo run --bin lfj-server                     # 127.0.0.1:8080
LFJ_BIND_PORT=18080 cargo run --bin lfj-server # override port
```

Then exercise it:

```bash
bash examples/call_api.sh                      # JSON + rejections + Arrow
```

## HTTP API

### `POST /api/v1/join` → JSON

```json
{
  "relations": [
    {"name": "E", "schema": [{"name":"a","type":"int64"},{"name":"b","type":"int64"}],
     "rows": [[0,1],[1,0]]}
  ],
  "fixtures": [],
  "select": ["a", "b"],
  "limit": 100,
  "cursor": null,
  "null_policy": "reject",
  "request_id": "optional-correlation-id"
}
```

* Either inline `relations` or built-in `fixtures` names (or both) are supplied.
* `select` defaults to the canonical order (join variables first, then private
  attributes in relation order).
* `limit` defaults to 100, capped at 1,000,000. When more rows remain, the
  response sets `truncated: true`, `decision: "undecidable"`, and returns
  `next_cursor`; pass it back as `cursor` to resume with no gaps/duplicates.
* `null_policy` is `reject` (default; fail on NULL join keys) or
  `drop_join_rows` (SQL semantics; dropped rows counted in diagnostics).

### `POST /api/v1/join/arrow` → Arrow streaming IPC

Same JSON request; the body is an `application/vnd.apache.arrow.stream`.
Pagination metadata travels in `X-LFJ-Request-Id`, `X-LFJ-Truncated`, and
`X-LFJ-Next-Cursor` headers.

### Diagnostics

Every response (success or error) carries a `request_id` and a `diagnostics`
object explaining **why it was accepted, rejected, or is undecidable**, along
with relation/column names, join variables, input/distinct counts, dropped-NULL
counts, and engine access counters. **No row values are ever logged** — only
structural metadata and counters (see the `redaction` field).

### Restricted join — what is rejected

| Code | Reason |
|------|--------|
| `disconnected_join_graph` | Relations do not form one connected component → Cartesian product |
| `no_common_attribute` | Multiple relations share no attribute |
| `type_mismatch` | A shared attribute has incompatible types |
| `null_in_join_key` | NULL on a join key under `reject` |
| `row_arity_mismatch` / `type_mismatch` | Rows do not match the declared schema |
| `variable_not_bound` / `duplicate_variable` | Invalid `select` |
| `invalid_cursor` | Malformed/typed/arity-mismatched resumption token |
| `too_many_relations` / `bad_limit` / `missing_relation` | Resource/input limits |

Validation errors return HTTP 400 with `decision: "rejected"`; an over-large
body returns 413; a valid page that hit the limit is 200 with
`decision: "undecidable"`.

## Built-in fixtures

`tri_e/tri_f/tri_g` (triangle over K4 → 24 orientations), `skew_ab/skew_ac/
pick_b` (million-row naive intermediate, 1000-row answer), `sp_x/sp_y`
(2k-row far-apart domains intersecting on 3 keys), `dup_l/dup_r` (multiplicity
3×2=6). See `src/fixtures.rs` and the smaller files under `fixtures/`.

## Verification artifacts

* `docs/VERIFICATION.md` — recorded results and how they were produced.
* `docs/ALGORITHM.md` — trie ordering, leapfrog step, multiplicity, resumption.
