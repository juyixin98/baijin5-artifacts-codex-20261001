# recursive-cte-backend

A bounded execution backend for a **restricted `WITH RECURSIVE` subset**, written
in Rust on **Axum + Arrow2**. It supports `UNION` / `UNION ALL` and
**path-aware cycle marking on a declared key**, with explicit depth/row
resource bounds and a configurable deterministic traversal order.

The engine is deliberately not a SQL parser: the supported recursive shape is
declared structurally as JSON and fully validated before anything runs.

---

## 1. Supported subset and key semantics

A request describes exactly one recursive view `R`:

```text
R = seed
    UNION [ALL]
    SELECT project
    FROM   R JOIN edges ON R.<k> = edges.<ek> [ AND ... ]
```

* **Seed term**: typed literal rows.
* **Recursive term**: one equi-join of `R` against a named edge relation, with
  a restricted projection expression language (column references from either
  side, typed literals, checked integer `add`/`sub`).
* **Set quantifier**:
  * `union` — global set semantics. Rows are deduplicated on the SQL `SET`
    columns (every user column plus the cycle marker; the path column is
    excluded, because it changes on every step).
  * `union_all` — bag semantics. Parallel / repeated edges preserve
    multiplicity; the only thing that stops a branch is the path-cycle test or
    a bound.
* **Cycle handling** (optional, configured explicitly):
  * a single **declared key column** (`int64` or `utf8`),
  * an engine-managed **path list column**, initialized to `[key]` at the seed
    and extended with the child key each step,
  * an engine-managed **boolean marker column**.
  * `mode = mark` (default): when the child's key occurs among its branch's
    **ancestors**, the revisiting row is emitted once with `cycle = true` and
    that branch halts. `mode = error`: the revisit is a hard, categorized
    failure.
* **Bounds**: `max_depth` and `max_rows`. When a bound prevents further
  emissions the response status is `incomplete` with a precise
  `incomplete_reason` (`max_depth` / `max_rows`) — results are never silently
  truncated and never reported as complete.
* **Traversal order**: `bfs` (level-synchronous semi-naïve rounds) or `dfs`
  (explicit-stack pre-order). Both are fully deterministic and produce the
  same row multiset; output ordering differs as documented.

### Why cycle detection is key-based

The path column grows by one element every iteration. If cycle detection
fingerprinted the whole row — including the path — every successive row around
a cycle would look different and the walk would never notice the repeat. Cycle
membership is therefore a **branch-local test of the declared key against the
ancestor path**, which is orthogonal to the global `UNION` set dedup.

### The working table holds only new rows

Fixpoint evaluation is semi-naïve: each round rescans only the current
frontier (rows not yet expanded). Previously materialized rows live in the
result sink and are never re-scanned.

---

## 2. Project layout

```text
src/
├── main.rs             # Axum server binary
├── lib.rs              # public API surface
├── plan.rs             # wire model + cross-field request validation
├── error.rs            # categorized error type (never collapses to success)
├── config.rs           # independent process configuration layer (env-driven)
├── batch/              # typed scalar model + typed batches + Arrow2 conversion
│   ├── mod.rs
│   ├── value.rs
│   └── tests_unit.rs
├── operator/           # query operators (no recursion knowledge)
│   ├── expr.rs         #   typed expression evaluation (checked arithmetic)
│   └── join.rs         #   stable in-memory equi-join (bag multiplicity)
├── exec/               # resources & state
│   ├── seed.rs         #   seed assembly + managed-column initialization
│   ├── state.rs        #   frontier policy, SET set, path-cycle classification
│   └── engine.rs       #   BFS/DFS fixpoint loop, bounds, logging, IPC sink
├── reference.rs        # INDEPENDENT explicit-recursion oracle (test oracle)
└── api/mod.rs          # HTTP validation/execution entry points
tests/
├── common/mod.rs       # fixtures + engine-vs-oracle cross-check harness
├── tree_test.rs        # rooted tree: exact rows, exact paths, order, logs
├── graph_test.rs       # multi-parent diamond, self loop, 2-cycle, dup edges
├── limits_test.rs      # max_depth / max_rows completeness categories
├── validation_test.rs  # concrete failure categories + typed data rejection
├── arrow_test.rs       # Arrow IPC stream decode + physical type/value checks
└── http_test.rs        # real Axum server over loopback HTTP/1.1
examples/
├── self_loop.json
├── diamond_union_all.json
└── ...
```

The independent oracle in `src/reference.rs` uses its own JSON scalars, its
own expression evaluator, and its own explicit-recursion walk. It shares only
the request data model with the engine — **expected answers are not generated
by the code under test**.

---

## 3. Local startup

Requires a recent stable Rust (developed/locked on 1.98.1). All third-party
crates are pinned in `Cargo.lock`.

```bash
# build
cargo build --release

# run the server (defaults shown)
CTE_HOST=127.0.0.1 CTE_PORT=8080 \
CTE_DEFAULT_MAX_DEPTH=64 CTE_DEFAULT_MAX_ROWS=10000 CTE_HARD_MAX_ROWS=1000000 \
CTE_MAX_REQUEST_BYTES=4194304 CTE_LOG_FORMAT=text \
./target/release/recursive-cte-backend
```

Configuration is entirely environment-driven and independent of query
semantics:

| Variable | Default | Meaning |
|---|---|---|
| `CTE_HOST` | `127.0.0.1` | bind IP |
| `CTE_PORT` | `8080` | bind port |
| `CTE_MAX_REQUEST_BYTES` | `4194304` | reject larger JSON bodies (HTTP 413) |
| `CTE_DEFAULT_MAX_DEPTH` | `64` | default depth bound |
| `CTE_DEFAULT_MAX_ROWS` | `10000` | default row bound |
| `CTE_HARD_MAX_ROWS` | `1000000` | ceiling a request may never exceed |
| `CTE_LOG_FORMAT` | `text` | `text` or `json` |
| `CTE_LOG` | `info` | tracing filter |

Run the tests:

```bash
cargo test                       # full suite (unit + integration)
cargo clippy --all-targets -- -D warnings
cargo fmt
```

---

## 4. Example requests

### 4.1 Self loop with path + cycle marker

```bash
curl -sS 127.0.0.1:8080/v1/recursive/execute \
  -H 'Content-Type: application/json' \
  --data @examples/self_loop.json
```

Output (abridged):

```json
{
  "run_id": "run-...-....",
  "engine_version": "0.1.0",
  "status": "complete",
  "incomplete_reason": null,
  "stats": { "iterations": 2, "output_rows": 3, "cycles_marked": 1,
             "duplicates_suppressed": 0, "join_probes": 3 },
  "output": {
    "columns": [
      {"name":"node","type":"int64"},
      {"name":"depth","type":"int64"},
      {"name":"path","type":"list<int64>"},
      {"name":"is_cycle","type":"bool"}
    ],
    "rows": [
      [1, 0, [1], false],
      [2, 1, [1, 2], false],
      [2, 2, [1, 2, 2], true]
    ]
  },
  "arrow_ipc_base64": "....",
  "log": [ ... step-by-step entries tied to run_id ... ]
}
```

The seed JSON provides only the **user** columns (`node`, `depth`); the
`path` and `is_cycle` columns are declared in the schema but initialized and
maintained by the engine.

### 4.2 Diamond under `UNION ALL` (two parents preserved)

```bash
curl -sS 127.0.0.1:8080/v1/recursive/execute \
  -H 'Content-Type: application/json' \
  --data @examples/diamond_union_all.json
```

Node 4 appears **twice**, once per path: `[1,2,4]` and `[1,3,4]`. The same
graph under `union` emits node 4 once and counts one suppressed duplicate.

### 4.3 Validation only

```bash
curl -sS 127.0.0.1:8080/v1/recursive/validate \
  -H 'Content-Type: application/json' --data @examples/self_loop.json
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/recursive/validate` | validate only; never runs the fixpoint |
| `POST` | `/v1/recursive/execute` | validate and execute |
| `GET`  | `/v1/version` | engine version |
| `GET`  | `/healthz` | liveness |

Failure responses carry a concrete category, e.g.

```json
{ "run_id":"run-...", "engine_version":"0.1.0", "status":"failed",
  "failure_category":"invalid_plan",
  "message":"recursive_term.on must declare at least one equi-join key" }
```

| Category | HTTP | Meaning |
|---|---|---|
| `invalid_plan` | 400 | structurally/semantically invalid request |
| `invalid_data` | 422 | typed data violation, arithmetic overflow, `cycle.mode = error` |
| `resource_limit` | 422 | server hard ceiling / policy rejection |
| `internal` | 500 | unexpected internal failure |

`incomplete` runs (bound hit) are still HTTP 200 with `status: "incomplete"`.

---

## 5. Evidence strategy

* **Independent oracle**: every behavioral integration test runs the same
  request through both the production engine and the explicit-recursion oracle
  and compares the full row multiset, cycle/duplicate counters, and the
  terminal verdict (including the exact incompleteness reason).
* **Concrete assertions**: tests assert exact rows, exact path contents, exact
  order, and exact failure categories — not merely that an endpoint responds.
* **Fixtures**: rooted tree, multi-parent diamond, self loop, 2-cycle, parallel
  duplicate edges, multiple seeds, text and integer keys.
* **Traceable logs**: each response embeds structured log entries carrying the
  run id, engine version, per-round frontier/emitted/cycle/duplicate counters,
  and the textual decision basis. Server logs use the same run id.
* **Arrow evidence**: tests decode the base64 Arrow IPC stream independently
  and assert physical types (`Int64`, `LargeUtf8`, `LargeList`, `Boolean`) and
  values.
* **Unknown states are not successes**: validation errors are `failed` with a
  category; bounds are `incomplete` with a reason; there is no path by which an
  exception is reported as success.

---

## 6. Key trade-offs and limits

* **Declarative subset, not SQL**: one seed, one edge join, one recursion; no
  arbitrary SQL, aggregates, `LIMIT`, or mutual recursion. This keeps plans
  fully analyzable and bounds enforceable up front.
* **Single declared key** for path-based cycle marking (int64/utf8). Composite
  keys are not modeled; global `UNION` dedup still works over all columns.
* Path/cycle columns are engine-managed: the engine owns their initialization
  and extension; user projections must not target them (validated).
* Memory use is `O(result rows + frontier)`, suitable for bounded in-process
  runs; the row/request caps bound it.
* BFS and DFS agree on the result multiset but DFS emits in pre-order; neither
  is a substitute for SQL's unspecified row ordering.
