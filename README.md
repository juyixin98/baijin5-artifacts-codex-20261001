# NJ Backend — Neighbor Joining over synthetic distance matrices

FastAPI + NumPy + SQLite backend that builds phylogenetic trees from
synthetic distance matrices (or synthetic FASTA sequences) with the
Neighbor Joining algorithm. All inputs are local synthetic fixtures; no
external accounts or real business data are involved.

## Layout

```
app/
  parsing.py     FASTA + p-distance boundary (input errors only)
  validation.py  matrix preconditions (symmetric / zero diagonal / non-negative)
  nj.py          Neighbor Joining core (stable ties, negative-branch modes)
  newick.py      Newick serializer + leaf identity map
  residuals.py   patristic distances + residual report
  store.py       SQLite provenance (one row per run, idempotency keys)
  service.py     pipeline orchestration and the inter-module error contract
  main.py        FastAPI boundary, error-category -> HTTP status mapping
  runlog.py      structured JSON run log (run_id, step, reason, data)
fixtures/        generated synthetic fixtures with hand-derived references
scripts/
  generate_fixtures.py  regenerates fixtures/ (references are hardcoded,
                        never produced by the NJ core under test)
  independent_newick.py minimal Newick parser used ONLY by tests/verify
  verify.py             end-to-end verification against the references
tests/           pytest suite (55 tests)
```

## Run

```bash
pip install -r requirements.txt
python3 scripts/generate_fixtures.py   # regenerate fixtures (idempotent)
python3 scripts/verify.py              # end-to-end verification, exit != 0 on failure
python3 -m pytest tests/ -q --cov=app  # test suite
uvicorn app.main:app                   # serve (NJ_DB_PATH / NJ_LOG_FILE to relocate)
```

`POST /v1/trees` builds a tree; `GET /v1/runs/{run_id}` returns the
persisted provenance record (request hash, params, events, residuals,
Newick, status — including failed runs).

## Boundary semantics (the contracts the tests pin down)

### Matrix preconditions — checked first, in this order

1. labels: ≥ 2 taxa, ≤ `max_taxa`, unique, non-blank;
2. shape: square, row count == label count;
3. finiteness: no NaN/inf;
4. non-negativity: no negative distances;
5. zero diagonal: exact `0.0`;
6. symmetry: exact equality by default (`symmetry_tol=0.0`; a caller may
   explicitly pass a tolerance — the default deliberately does not let
   quietly-wrong matrices through).

Non-additivity is **not** a precondition. Any matrix passing the checks
above is accepted; the fit error is reported through the residual report
(`sum_abs`, `max_abs`, `mean_abs`, `rms`, and a per-pair table), computed
from patristic distances on the *emitted* tree.

### Tie-breaking (stable)

Pairs are enumerated in ascending `(node_id_i, node_id_j)` order — leaf
ids follow input label order, internal nodes take sequential ids as
created. The minimum Q uses exact float equality; the first pair at the
minimum wins. Every tie emits a `q_tie` event with all candidates, the
chosen pair, and the rule, so the decision is replayable from the log.
Same input + same options ⇒ byte-identical Newick.

### Negative branch lengths — per declared mode, never silent

`options.negative_branch_mode`:

- `allow` (default): negative lengths are kept in the Newick and a
  `negative_branch` event is recorded;
- `clamp`: the branch is set to `0.0` and a `branch_clamped` event with
  the raw value is recorded. The distortion is **not** hidden: the
  residual report is computed from the emitted tree, so clamping shows
  up as fit error (fixture `negative_branch_4taxon`: residual goes from
  0.0 to 10.5 under clamp);
- `error`: the run aborts with `COMPUTATION_FAILED`.

### Output

Newick uses synthetic leaf names `L<node_id>`; `leaf_map` maps them back
to the original labels, so arbitrary label text never needs Newick
escaping. NJ is unrooted: the final edge is represented by a synthetic
root at its midpoint (each side `d/2`). This preserves every patristic
distance and is a representation choice, not a biological claim.

### Error taxonomy (distinguishable everywhere)

| category            | HTTP | meaning                                            |
|---------------------|------|----------------------------------------------------|
| `INPUT_VALIDATION`  | 422  | malformed FASTA/matrix, failed preconditions       |
| `STATE_CONFLICT`    | 409  | idempotency key replayed with a different payload  |
| `RESOURCE_EXHAUSTED`| 413  | taxa count above `max_taxa`                        |
| `COMPUTATION_FAILED`| 500  | numeric pipeline could not produce a result        |
| `NOT_FOUND`         | 404  | unknown `run_id`                                   |

Error bodies carry `category`, `message`, `details`, and the `run_id`;
failures are persisted as `status="error"` run records (except
idempotency conflicts, which never touch the original record).

### Idempotency

`request_id` is an idempotency key: same key + identical payload ⇒ the
stored result is returned with `replayed=true`; same key + different
payload ⇒ `409 STATE_CONFLICT`.

### Logging

One JSON object per line with `run_id`, `step`, `reason`, and `data`
(input hash, n_taxa, Q values, tie candidates/choice, clamped raw
values, residual summary). Given a `run_id`, the log plus the SQLite
record replays every decision of the run.

## Checks deliberately NOT performed (not "passed", just out of scope)

- **Triangle-inequality / metric validation**: beyond non-negativity and
  symmetry, distances are not required to be metric; NJ tolerates this
  and the residual report carries the consequences.
- **Ultrametricity / molecular-clock checks**: NJ does not assume them.
- **Approximate tie detection**: ties use exact float equality; two Q
  values differing by 1e-17 are not a tie and produce no event.
- **Gap/ambiguity handling in sequences**: the alphabet is strictly
  A/C/G/T; anything else is an input error, not a guess.
- **Authentication, rate limiting, multi-tenancy**: local synthetic
  service; the HTTP layer is a thin boundary over the pipeline.
- **Concurrency hardening beyond a single process**: SQLite is guarded
  by a process-local lock; no cross-process coordination.
- **Tree comparison metrics** (Robinson–Foulds etc.): verification uses
  exact Newick equality and patristic-distance agreement instead.
