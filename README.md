# pctl — grouped exact percentiles, mode & ordered string aggregation

A pure-backend service implementing three exact grouped aggregation operators
on typed batches, with a **bounded external-sort** execution engine.

- Stack: **Rust · Axum · Arrow2** (no database, no external services).
- All data is local synthetic JSON; nothing here requires a production account.

## Operators and semantics

NULLs are filtered *before* ranking and never occupy a rank. Equal keys keep a
stable, global ingest-ordinal order under every operator.

| Operator | Semantics |
|---|---|
| `percentile` `continuous` | `N` non-null sorted values `v₀…`, `h=(N−1)·p`; result `v⌊h⌋ + frac(h)·(v⌈h⌉−v⌊h⌋)` as `f64`. `p∈[0,1]`. |
| `percentile` `discrete` | value at 1-based rank `ceil(p·N)` (pinned to rank 1 for `p=0`); original type preserved (`i64`/`f64`/`utf8`). |
| `mode` | most frequent value; on a tie the smallest key wins and `"tie": true` is reported with the shared `frequency`. |
| `string_agg` | non-null strings concatenated in sorted order (`asc`/`desc`) with a required delimiter. |

A measure containing `NaN` makes a percentile **indeterminate** (no total
order) and is reported with `status: "indeterminate", code: "nan_measure"`
rather than silently ordered.

## Resource & state model

- **Resident memory budget** is shared per query and charged for every entry
  **including UTF-8 payload bytes**. Overflow spills the largest resident
  (group, operator) buffer as a sorted run, so one skewed heavy group keeps
  flushing itself instead of causing unbounded growth.
- **Distinct groups are hard-capped** (`group_table_cap`); exceeding it fails
  fast with `groups_cap_exceeded` instead of accumulating objects without
  bound.
- **External sort**: runs are length-delimited binary files (`PSR1`), merged
  with a bounded k-way heap (one head entry per run).
- **Cancellation is cooperative at whole-row boundaries**. On cancel, all
  resident buffers are flushed and `checkpoint.json` is atomically published;
  the response carries a resume token (`spill_dir`, `ordinal_cursor`). Resume
  verifies a fingerprint of the query shape *and payload*, then continues at
  exactly the cursor row.
- Quantile parameters out of `[0,1]` (or non-finite) are rejected **before any
  execution state is created**.

## Project layout

```
src/
  spec.rs          wire JSON shapes + validated QueryPlan
  validate.rs      single execution-front validation gate
  batch.rs         JSON -> typed Arrow2 columns (Int64/Float64/Utf8)
  operator/        pure percentile/mode/string-agg math over sorted keys
  resources.rs     memory budget (incl. string bytes), spill quota, cancel token
  exec/
    codec.rs       spill run binary format
    merge.rs       bounded k-way merge
    state.rs       bounded group registry + checkpoint/recovery
    mod.rs         ingest → spill → merge → operator state machines
  api/             Axum router + handlers (request ids, typed errors, logs)
  diagnostics.rs   request ids, redaction, structured accept/reject logs
  config.rs        config/pctl.toml + PCTL_* env overrides
tests/
  common/mod.rs    INDEPENDENT naive reference evaluator + fixtures
  hand_computed.rs hand-derived values + reference cross-check
  validation.rs    rejection categories & NaN indeterminacy
  spill_resume.rs  forced external sort, cancel/resume, cap enforcement
  http_api.rs      end-to-end Axum tests (status codes, ids, error bodies)
  data/            sample request fixture
config/pctl.toml   startup configuration
```

## Run it

Dependencies are vendored under `vendor/` and wired in `.cargo/config.toml`,
so the project builds **fully offline** (`--offline`) with no registry access
and no contention on a shared cargo cache.

```bash
# from the project root
cargo run --offline --release --bin pctl-server
# or point at an explicit config:
cargo run --offline --release --bin pctl-server -- config/pctl.toml
```

Environment overrides: `PCTL_BIND_ADDR`, `PCTL_MEMORY_BUDGET_BYTES`,
`PCTL_SPILL_DIR`, `PCTL_SPILL_MAX_BYTES`, `PCTL_GROUP_TABLE_CAP`.

### Example request

```bash
curl -sS localhost:8080/query -H 'content-type: application/json' \
  -d '{
    "group_by": "g",
    "columns": [
      {"name":"g","data_type":"utf8","values":["a","a","b","b","b"]},
      {"name":"v","data_type":"i64","values":[1,3,2,2,10]}
    ],
    "operators": [
      {"op":"percentile","column":"v","p":0.5,"method":"continuous"},
      {"op":"mode","column":"v"},
      {"op":"string_agg","column":"g","delimiter":",","order":"asc"}
    ]
  }'
```

HTTP status mapping: `200` complete/checkpoint, `400` validation or malformed
JSON, `422` indeterminate value, `507` resource policy. Every response carries
an echoed or generated `x-request-id` header and a matching `request_id` body
field plus a `diagnostics` block (budget high-water mark, groups, runs and
bytes spilled, resume token).

### Local fault injection / resume

`hints` are local-only test controls:

- `memory_budget_bytes`: per-request budget override (forces spilling).
- `cancel_after_runs`: cancel cooperatively right after the Nth durable run.
- `resume`: `{ "spill_dir": …, "ordinal_cursor": … }` from a prior cancelled
  response.

## Tests

```bash
cargo test --offline                 # all unit + integration tests (28 cases)
cargo test --offline --test hand_computed
cargo clippy --all-targets           # clean: no warnings
```

### Verified results (captured on this machine)

`cargo test --offline` — **28 passed, 0 failed**:

- lib unit tests `8 passed`: hand-computed `percentile_cont/disc` on the even
  sample `[10,20,30,40]` (`25`, `17.5`, `37`; disc rank-2 `20`), mode unique /
  two-way / three-way ties, ordered string aggregation, rank selectors.
- `hand_computed` `3 passed`: even sample, an all-NULL group (SQL NULL result,
  `non_null=0`, but `rows=3`), equal-frequency mode tie, a six-fold repeated
  group, NULLs not occupying ranks, stable equal-value order — every value also
  cross-checked against the independent reference evaluator.
- `validation` `7 passed`: `p>1`, `p<0`, NaN/±∞ quantiles rejected
  (`quantile_out_of_range` / `quantile_not_finite` at `operators[i].p`) before
  execution; unknown column / type mismatch / empty delimiter; cell type
  mismatch and ragged columns; NaN measure -> `indeterminate/nan_measure`.
- `spill_resume` `4 passed`: with a forced **64 KiB** budget over 2k skewed
  rows, hundreds of runs spill and every group/op matches the reference;
  `cancel_after_runs=1` -> durable checkpoint -> resume completes with
  bit-identical results and correct row counts (no double counting); resume
  with a changed quantile is rejected (`resume_plan_mismatch`); distinct-group
  cap enforced (`groups_cap_exceeded`).
- `http_api` `6 passed`: happy path, echoed/generated `x-request-id`,
  `400` quantile/malformed bodies, `507` resource cap, `/health`.

End-to-end against the running server (6,000-row skewed dataset, 64 KiB
budget): first request returned `200 status="cancelled"` after a durable run
boundary (29 runs, cursor 287); resuming with the token returned
`200 status="complete"` over 7 groups (284 runs / ~410 KiB spilled), and every
group matched an **independent Python** percentile/mode/string-agg reference.
The spill directory is empty afterward (successful cleanup).

The integration suites assert concrete results and concrete failure
categories/status codes — not merely that an endpoint responds. Expected values
are hand-derived in `tests/hand_computed.rs` and independently re-derived by a
naive `HashMap`/std-sort evaluator in `tests/common/mod.rs` that never calls the
production engine, so the implementation cannot self-certify. The external-sort
suite forces spilling with a tiny budget and verifies cancel→checkpoint→resume
returns bit-identical results to a non-cancelled run.

## Diagnostics & sensitive data

Logs are structured JSON with `request_id`, `decision` (accept/reject/
indeterminate), stage, budget state and counts. Column **payloads are never
logged**; helpers in `diagnostics.rs` render string values only as a
length + non-reversible hash fingerprint (`redact`).
