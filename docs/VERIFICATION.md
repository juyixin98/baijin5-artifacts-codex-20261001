# Verification report

All commands were run on Linux x86_64 with Rust 1.98.1. Everything is local and
deterministic; no external accounts or business data are used.

## How to reproduce

```bash
cargo test                                        # full suite
cargo clippy --all-targets -- -D warnings         # zero warnings
cargo build --bin lfj-server
LFJ_BIND_PORT=18080 ./target/debug/lfj-server &   # start
bash examples/call_api.sh                         # live HTTP walkthrough
```

`Cargo.lock` pins every transitive dependency for reproducible builds.

## Automated tests (35 total, all passing)

| Test file | Count | What it proves |
|-----------|------:|----------------|
| `src/domain` unit | 2 | SQL NULL-never-equal semantics; total order used by sorting/binary search |
| `triangle_test.rs` | 3 | K4 triangle = **24** oriented tuples (hand-computed constant); exact multiset match to the independent oracle; logarithmic-ish seek work (no pairwise scans) |
| `skew_sparse_test.rs` | 3 | Skew = **1000** exact rows with naive intermediate **≥ 1,000,000** while LFJ stays sparse; sparse domains intersect in **4 seeks** |
| `pagination_test.rs` | 7 | Lexicographic order; pages stitch with **no gaps/duplicates** (27 rows / page 5 → 6 pages); garbage/wrong-arity cursors rejected; projection multiplicity; groups never split; duplicate multiplicity 3×2=6 |
| `validation_test.rs` | 11 | Each rejection asserts the **specific machine code/category**: disconnected graph, dangling relation, type mismatch, NULL join key, row arity, unbound select, unknown fixture, empty list, duplicate column, excessive limit, drop-and-count policy |
| `http_test.rs` | 6 | Live router: health, triangle fixture, 400 + code for Cartesian product, Arrow pagination headers, **413** oversized body, echoed request id, multiplicity 6 |
| `arrow_roundtrip_test.rs` | 2 | IPC stream decoded with arrow2's own reader → expected schema/values; NULL private column round-trips |
| `differential_test.rs` | 1 | **150 deterministic random** connected chains (2–4 relations, duplicates, random projections, page sizes 1–5) vs the independent oracle: exact values + multiplicities, strictly sorted stitch |

Answers are never produced by the code under test: they come from hand-computed
constants and the separate, hash-map-based naive oracle in
`src/join/naive.rs`, which shares no code with the engine.

## Recorded live results

Triangle over built-in K4 fixtures:

```
decision: accepted   row_count: 24   emitted_multiplicity: 24
counters: seek_calls 21, seek_key_comparisons 43, next_calls 80, value_probes 305
```

Skew (`ab ⋈ ac ⋈ pick_b`), page 1 with `limit=100`:

```
decision: undecidable  rows: 100  truncated: true
first [0,0,0]  last [0,0,99]  cursor present
resume -> rows: 100, first [0,0,100]      # boundary continues exactly
```

The naive oracle reports `max_intermediate = 1,000,000` for this query
(`1001 × 1000` nested-loop probes; the `a=1` row has no match, so exactly
1,000,000 joined rows are materialized); the final answer is only 1000 rows,
and LFJ never allocates that product (asserted in
`skew_avoids_million_row_intermediate`).

Sparse intersection (2000 vs 2004 rows, far-apart domains):

```
rows: [5,6,50], [7,8,70], [9,10,90]
counters: seek_calls 4, next_calls 6      # leaps, not a 2000-step merge
```

Duplicates: `(1,10,100)` multiplicity **6**.

NULL policy `drop_join_rows`: result `(k=5,y=1,z=2)` with
`null_join_rows_dropped: [1, 0]` and wire label `drop_join_rows`.

Rejection example (Cartesian product):

```
HTTP 400
decision: rejected  category: validation  code: disconnected_join_graph
message: relation 'b' shares no attribute with the connected component of 'a';
         joining it would form a Cartesian product
```

Arrow endpoint: `Content-Type: application/vnd.apache.arrow.stream`,
`X-LFJ-Truncated`, `X-LFJ-Next-Cursor`, `X-LFJ-Request-Id`; the stream begins
with the IPC continuation marker and ends with the EOS marker, and arrow2
decodes it back to the expected rows.

## Diagnostics / redaction

Every response carries a `request_id` (caller-supplied or generated) and a
`diagnostics` object stating the decision (`accepted` / `rejected` /
`undecidable`), the reason, join variables, per-relation input/distinct counts,
dropped-NULL counts, and engine counters. The `redaction` field records the
policy: **row values are never logged**, only structural metadata and counters.
