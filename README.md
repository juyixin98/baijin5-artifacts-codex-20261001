# IEJoin-style Two-Predicate Range Join Backend

A batch range-join engine that evaluates the conjunction of **two inequality
predicates** between two typed, Arrow2-backed tables, in the style of the
IEJoin algorithm: two sorted permutations, inverse position maps, a
monotonically updated candidate bitmap, and strict/non-strict equal-group
boundaries. It is exposed as an in-process Rust library and an Axum HTTP
service.

Everything runs locally on synthetic fixtures — no external accounts or
business data are required.

---

## 1. What is implemented

| Requirement | Where |
|---|---|
| Typed batches over Arrow2 arrays | `src/types/` (`TypedBatch`, `Column`, `Scalar`) |
| Query operator with two range predicates, four directions | `src/operator/plan.rs`, `src/operator/iejoin.rs` |
| Two sort permutations + inverse position maps | `src/permutation.rs` |
| Strict vs non-strict equal-value boundaries | `SortedColumn::asc_prefix_len` / `desc_prefix_len` / `match_interval` |
| Duplicate rows keep identity and combinatorial multiplicity | stable permutations; physical row id is the identity |
| NULL never matches | NULL sort-prefix excluded from every activation/probe region |
| Candidate bitmap update cannot skip equal groups | monotonic prefix activation keyed only on the driver value |
| Candidate-access counting | `JoinStats.candidate_accesses` (one count per bitmap probe) |
| Resource limits + explicit truncation or controlled paging | `src/resource.rs`, `src/state.rs` |
| Four distinguishable failure classes | `src/error.rs`: input / state / resource / compute |
| Validation entry point | `src/validation.rs` |
| Replay records (run id, fingerprints, checkpoint, rationale) | `src/replay.rs` |
| Axum HTTP boundary | `src/api/` |
| Independent nested-loop oracle (not the tested core) | `src/operator/nested_loop.rs` |
| Reusable synthetic fixtures | `tests/common/mod.rs` |
| Verification script | `scripts/verify.sh` |

### Module contracts

* **Data contract** — operators consume `TypedBatch` (columns of `Int64`,
  `Float64` or `Utf8`, all Arrow2) and a `JoinPlan` of exactly two
  `Predicate { left_col, right_col, op }` with `op ∈ {lt, le, gt, ge}`.
  Output is `OutputPair { left_row, right_row }` identified by **physical row
  index**, which is why duplicates retain multiplicity.
* **Error contract** — every fallible boundary returns `JoinError { code,
  message }`. `ErrorCode::category()` yields one of `Input | State |
  Resource | Compute`. HTTP status mapping is centralized in
  `src/api/http.rs` (400 input, 409 state, 404 unknown run, 413 output over
  budget, 503 session-capacity, 500 compute).

---

## 2. Boundary semantics (read this before judging results)

1. **The four directions.** Predicates are written `left OP right`. `lt`/`le`
   make the left side the smaller operand; `gt`/`ge` make it the greater
   operand. Both predicates are canonicalized internally to
   `smaller <|<= greater`; the driver is the greater side of predicate 1 and
   the active bitmap side is its smaller side. Predicate 2 is then either a
   descending **suffix** (active side smaller on p2) or **prefix** (active
   side greater on p2). All four comparator directions plus both mixed
   orientations (`lt/gt`, `ge/le`) are differential-tested.

2. **Strict vs non-strict on equal groups.** For `l.k <= r.k` the equal run is
   included; the activation prefix extends **through** it. For `l.k < r.k` it
   stops **before** it. Consequently, over all-equal keys a strict join emits
   nothing while a non-strict join emits the full Cartesian product (with
   multiplicity). This is asserted explicitly.

3. **Duplicates.** Values repeat freely; the identity of a row is its physical
   index. Stable sorting guarantees two equal-value rows occupy distinct
   sorted slots and both match independently. The tests compare result
   **multisets**, not deduplicated sets.

4. **NULL.** NULL sorts first deterministically (for stable permutation only)
   but is excluded from activation prefixes and probe regions, so **no pair is
   emitted when either operand of either predicate is NULL**. Float `NaN` is
   canonicalized to NULL; `-0.0 == 0.0`.

5. **Bitmap update order.** Activation is a single monotonic boundary over the
   active side sorted ascending on predicate 1. It depends solely on the
   current driver key, so an entire equal-key driver group observes the
   identical bitmap — an equality group can never be split across iterations.

6. **Truncation and paging.** A one-shot `/join` that cannot fit its result in
   `max_output` (or would exceed `max_candidate_accesses`) fails with the
   resource error `budget_exceeded` **instead of silently dropping tail rows**.
   `/sessions` returns pages with a `next_cursor` carrying an opaque
   checkpoint; continuations resume exactly (`dpos`, `act_pos`, `probe_pos`,
   `probe_hi`) and the concatenation of pages equals the one-shot multiset.
   Replaying/stale/foreign cursors are distinct state errors.

7. **Candidate-access metric.** One count per bitmap membership probe while
   collecting predicate-2 candidates. It is deterministic for fixed inputs and
   is asserted to be below the nested-loop pair count on high-selectivity data.
   It is a work metric, not wall-clock time.

---

## 3. Build and test

Pinned, exact dependency versions are in `Cargo.toml`; the full transitive set
is frozen in `Cargo.lock`.

```bash
cargo test                       # all unit + integration tests
cargo test --test iejoin_core_test -- --nocapture
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
```

Or run everything (plus an HTTP smoke test if the server can bind):

```bash
./scripts/verify.sh
```

### Run the server

```bash
IEJOIN_HOST=127.0.0.1 IEJOIN_PORT=8080 cargo run --bin iejoin-server
# optional replay log file:
IEJOIN_REPLAY_LOG=./runs.jsonl cargo run --bin iejoin-server
```

### One-shot request

```bash
curl -s localhost:8080/join -H 'content-type: application/json' -d '{
  "plan": {"p1": {"left_col":0,"right_col":0,"op":"gt"},
           "p2": {"left_col":1,"right_col":1,"op":"lt"}},
  "left":  {"columns":[
    {"name":"a1","type":"int64","values":[5,2,5,9]},
    {"name":"a2","type":"int64","values":[7,7,1,3]}]},
  "right": {"columns":[
    {"name":"b1","type":"int64","values":[5,1,2,9]},
    {"name":"b2","type":"int64","values":[7,9,0,4]}]}
}'
```

Exact answer: pairs `(0,1),(1,1),(2,1),(3,0),(3,1)`.

### Paged session

`POST /sessions` with a `"budget": {"max_output": 1}`; then repeatedly
`POST /sessions/{id}/continue` with the returned `next_cursor` until
`finished: true`.

### Replay lookup

Every request returns a `run_id`; `GET /runs/{run_id}` returns the record:
plan, budget, per-column value fingerprints, the resumable checkpoint, stats,
outcome (`completed|truncated|failed`), error code/category on failure, and a
one-sentence rationale.

---

## 4. How tests independently establish correctness

The oracle is `src/operator/nested_loop.rs`, a brute-force evaluator that
shares **no** join logic with the IEJoin core (no permutation, bitmap, or
boundary code). In addition:

* `tests/iejoin_core_test.rs` — result **multisets** must equal the oracle for
  all six orientations over hand, all-equal, empty-side, high-selectivity,
  dense-duplicate, NULL and seeded-random datasets; plus hand-written exact
  pair lists and candidate-access bounds.
* `tests/failures_test.rs` — each failure asserts its **specific code and
  category**: bad column index, duplicate binding, invalid budget, column
  length mismatch, output budget, candidate budget, session capacity, unknown
  session, foreign cursor, finished session, stale cursor.
* `tests/replay_test.rs` — run ids, fingerprints, truncation markers and
  checkpoint-driven replay that reconstructs the full result.
* `tests/api_test.rs` — real in-process HTTP requests asserting exact pairs,
  pagination reassembly, and per-category status codes.

---

## 5. Checks that are NOT executed here

The following are deliberately **not** claimed as passing in this environment;
they are listed rather than faked:

* **Coverage threshold enforcement (`cargo llvm-cov`, 80%).** The tool is not
  installed and no network policy was assumed. Run
  `cargo install cargo-llvm-cov && cargo llvm-cov --fail-under-lines 80`.
* **`cargo audit` / `cargo deny`.** Not run; security/license scanning tools are
  not part of the pinned toolchain here.
* **Multi-node / persistence behavior.** Sessions and the replay ring are
  in-memory only; there is no database or cross-process recovery.
* **Authenticated/multi-tenant deployment.** The server binds localhost by
  design; there is no authn/authz layer.

These are scope boundaries, not silently-skipped assertions.
