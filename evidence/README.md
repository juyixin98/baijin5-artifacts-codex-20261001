# Evidence index

This directory records the evidence that the implementation actually works and
how correctness was checked. Regenerate everything from the repository root:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --no-fail-fast -- --nocapture > evidence/test-run-final.log 2>&1
echo $?     # 0 = all tests passed
```

## Files

| File | Contents |
|---|---|
| `test-run-final.log` | Final full `cargo test --nocapture` run. Every behavioral case prints a `[case itest-<input>-NNN]` line carrying the run identity, input name, engine version, quantifier/order, status + incompleteness reason, engine counters, and the independent oracle's counters/verdict. |
| `test-run.log` | Earlier full run snapshot (pre-wire-fix). Kept for history; the authoritative record is `test-run-final.log`. |
| `server.log` | JSON server log from the live HTTP run (startup banner; request tracing uses the `run_id` echoed in each response). |
| `self_loop.response.json` | Live `POST /v1/recursive/execute` on the self-loop fixture, including the base64 Arrow IPC payload and the step-by-step run log. |
| `diamond_union_all.response.json` | Live run proving `UNION ALL` keeps both paths to the shared node `4`. |
| `two_cycle_text.response.json` | Live run on a text-key 2-cycle (`b <-> c`) with marker. |
| `boundary.response.json` | Live run with `max_depth=1`: `status=incomplete`, `incomplete_reason=max_depth`. |

## Final result

```text
66 tests, 0 failed, 0 ignored (cargo test exit 0)
cargo fmt --check : clean
cargo clippy --all-targets -- -D warnings : exit 0
release build : OK (cargo build --release)
live HTTP smoke (examples/*) : all expected rows/statuses observed
```

Test breakdown: 13 library unit tests (typed batches, expression evaluation,
join), 3 configuration tests, and 50 integration tests across tree, graph,
limits, validation, Arrow IPC, HTTP, and wire-contract suites.

## How results are independently checked

Each graph/tree/limits fixture is executed twice:

1. by the production engine (`src/exec`), and
2. by the explicit-recursion oracle in `src/reference.rs`, which has its own
   JSON scalar model, its own expression evaluator, and its own BFS/recursive
   walk. It shares only the request type, never engine code.

The harness compares the full row multiset, cycle/duplicate counters, and the
terminal verdict (including the exact `max_depth`/`max_rows` reason). Tests
also assert concrete rows, path contents, ordering, HTTP status codes, Arrow
physical types, and failure categories — not merely that an endpoint responds.

## Issues found during verification and fixed (no outstanding failures)

These were caught by the evidence process itself; the final run is green:

1. **Whole-row vs declared-key cycle test** — an early build flagged every row
   as a cycle because the child path already ends in its own key. Fixed to test
   key membership against the ancestor path (path minus its final element);
   pinned by the self-loop / 2-cycle / diamond tests.
2. **Wire type spelling** — examples documented `list<int64>` while serde
   accepted only `list_int64`. Aligned the wire enum rename to the documented
   spelling and locked it with `tests/wire_test.rs`.
3. **`stats.seed_rows`** — it reported the final row count instead of the seed
   count. Fixed and re-verified against the live server.

There are no skipped or unexecuted tests in the final run (`0 ignored`).
