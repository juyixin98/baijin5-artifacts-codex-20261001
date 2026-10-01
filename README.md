# Leapfrog Triejoin backend (Rust + Axum + Arrow2)

A worst-case-optimal **Leapfrog Triejoin** backend for **three-table and
restricted multi-table natural joins**. It joins ordered Tries directly — it
never materialises a large two-table intermediate product and then filters it.

Everything runs locally against deterministic synthetic fixtures; no external
accounts or business data are required.

## What is implemented

| Requirement | Where |
|---|---|
| Ordered Tries per relation in a compatible variable order, explicit duplicate multiplicity | [`src/trie.rs`](src/trie.rs) |
| Typed domain values, relation schema, validation | [`src/value.rs`](src/value.rs), [`src/schema.rs`](src/schema.rs) |
| Typed Arrow2 batches (Int64 / Utf8 / Boolean, nullable) + IPC stream endpoint | [`src/batch.rs`](src/batch.rs), [`src/api.rs`](src/api.rs) |
| Natural-join validation: type compatibility, hypergraph connectivity, NULL policy, restricted envelope | [`src/plan.rs`](src/plan.rs) |
| Leapfrog query operator with domain-intersection leaps and per-access counters | [`src/lftj.rs`](src/lftj.rs) |
| Output cap + resumable interruption cursors (limit paging, access-budget abort) | [`src/lftj.rs`](src/lftj.rs), [`src/state.rs`](src/state.rs) |
| Independent naive nested-loops full-enumeration oracle | [`src/naive.rs`](src/naive.rs) |
| Resources / state / cursor store | [`src/state.rs`](src/state.rs) |
| Diagnostics with request id, key state, accept/reject/undecidable, redaction | [`src/diagnostics.rs`](src/diagnostics.rs) |
| Shared validation/execution entry point (HTTP and tests use the same door) | [`src/validate.rs`](src/validate.rs) |
| Axum HTTP transport | [`src/api.rs`](src/api.rs), [`src/bin/server.rs`](src/bin/server.rs) |
| Config independent of code | [`src/config.rs`](src/config.rs) |
| Tests and fixtures independent of `src` | [`tests/`](tests/), [`fixtures/`](fixtures/) |

### Restricted multi-table envelope

- 2 to `LFTJ_MAX_RELATIONS` (default **6**) relations.
- Same-named columns must have identical types; columns shared by ≥2 relations
  are natural-join keys.
- The relation hypergraph must be **connected** through shared columns. A join
  with a disconnected relation would be an unrestricted Cartesian product and is
  rejected with `unsupported_shape` / `disjoint_schema` rather than computed.

### NULL policy (explicit, no silent loss)

- `null_policy: "reject"` (default) — any NULL in a shared/key column rejects
  the request with `null_key`. Silent row dropping is not the default.
- `null_policy: "sql_match_never"` — SQL semantics: NULL never equals anything
  (not even another NULL); such rows are excluded from the join.

Non-key columns may contain NULL and flow through as Arrow nulls.

### Variable-domain leaping

At each global variable the iterators that bind it are sorted by remaining
sibling count and leap with binary-search `seek` to the running maximum. A
partition-point search is used, so **no candidate intersection key is ever
skipped** (see `seek_never_skips_intersection_keys`).

### Limits and resumable interruptions

- `limit` caps output rows; the response carries an opaque single-use
  `next_cursor`. Cursors are TTL-bounded, capacity-bounded, fingerprinted to
  the exact query, and resume **without loss or duplication**, including
  multiset copies that straddle a page boundary.
- `access_budget` bounds trie accesses; when spent before completeness is
  provable the verdict is `undecidable` (HTTP 202) and the response is still
  resumable via `next_cursor`.

## Quick start

```bash
# 1. regenerate deterministic fixtures (optional; committed copies exist)
cargo run --example generate_fixtures

# 2. run the full test suite (unit + integration)
cargo test

# 3. offline validation walkthrough (triangle + skew, prints both counters)
cargo run --example run_validation

# 4. start the HTTP server (fixtures embedded at compile time)
cargo run --bin lftj-server
# LFTJ_BIND_ADDR defaults to 127.0.0.1:8080
```

Service-call examples: see [`docs/API.md`](docs/API.md). A capture of a real
run with normal and abnormal results is in [`docs/RESULTS.md`](docs/RESULTS.md).

## Reproducing the required verification

The mandated verification process is encoded in tests with concrete assertions:

```bash
# triangle query vs hand-derived answer and naive oracle
cargo test --test triangle -- --nocapture

# highly-skewed / sparse intersection vs naive full enumeration
cargo test --test skew -- --nocapture

# 4-table chain, 5-table star, 6-table envelope, disconnected rejection
cargo test --test multitable -- --nocapture

# HTTP normal + abnormal categories, paging, budget, Arrow IPC
cargo test --test api
```

What they prove:

1. **Triangle query** — K5 returns exactly 10 triangles (K_n returns the
   hand-stated `C(n,3)`); results match the independent naive oracle; K200
   shows the oracle materialises 2,646,700 root/path/leaf tuples while the
   engine reports `intermediate_tuples_materialized = 0`.
2. **Highly skewed + sparse intersection** — R and S agree on a 200×200 hub
   (`b=1`) but T intersects only five pairs. The answer is exactly the five
   hand-derived rows; the naive oracle materialises **40,405** intermediate
   tuples (40,000 of them hub prefixes T rejects) while Leapfrog uses a small
   number of trie accesses and **zero** intermediates.
3. **Failure categories** — tests assert the concrete stable code
   (`malformed_json`, `invalid_request`, `unknown_relation`, `type_mismatch`,
   `disjoint_schema`, `null_key`, `unsupported_shape`, `invalid_cursor`), not
   merely that an endpoint responded.

Expected answers are hand-derived constants in [`tests/support/mod.rs`](tests/support/mod.rs);
they are never generated by the engine under test. The naive oracle is a
separate, deliberately-materialising implementation in [`src/naive.rs`](src/naive.rs).

## Configuration

Environment variables (see [`src/config.rs`](src/config.rs)):

| Variable | Default | Meaning |
|---|---|---|
| `LFTJ_BIND_ADDR` | `127.0.0.1:8080` | listen address |
| `LFTJ_MAX_RELATIONS` | `6` | restricted-envelope size |
| `LFTJ_DEFAULT_LIMIT` | `1024` | page size when unspecified |
| `LFTJ_MAX_LIMIT` | `100000` | hard per-page ceiling |
| `LFTJ_MAX_ACCESS_BUDGET` | `100000000` | max client access budget |
| `LFTJ_CURSOR_TTL_SECS` | `300` | cursor validity |
| `LFTJ_MAX_CURSORS` | `1024` | live cursor capacity (FIFO eviction) |

`RUST_LOG` controls tracing output (JSON in the server binary).

## Project layout

```
src/
  value.rs        domain types (int/string/bool, Cell, NULL policy notes)
  schema.rs       typed columns + relation multiset validation
  trie.rs         ordered Trie, multiplicity, seek/next cursors + counters
  plan.rs         natural join validation + adaptive variable order
  lftj.rs         Leapfrog Triejoin operator, paging + budget
  naive.rs        independent nested-loops reference oracle
  batch.rs        Arrow2 typed chunks + IPC serialisation
  diagnostics.rs  request ids, decision records, redaction
  config.rs       env configuration
  state.rs        catalog + bounded expiring cursor store
  validate.rs     shared validation/execution entry point
  api.rs          Axum router/handlers
  bin/server.rs   server binary
  bin/fixtures.rs embedded fixture loader
examples/
  generate_fixtures.rs  deterministic fixture generator
  run_validation.rs     offline verification walkthrough
tests/
  support/mod.rs  hand-derived expectations + fixture builders
  triangle.rs     triangle verification
  skew.rs         skew/sparse verification
  multitable.rs   restricted multi-table (4/5/6-table) verification
  api.rs          HTTP end-to-end tests
fixtures/         minimal synthetic JSON fixtures (committed)
docs/             API examples and captured results
```

## Dependency lockpin

`Cargo.lock` is committed so every reproduction uses identical dependency
versions. Refresh deliberately with `cargo update`; audit as needed with
`cargo audit` / `cargo deny` in your own environment.
