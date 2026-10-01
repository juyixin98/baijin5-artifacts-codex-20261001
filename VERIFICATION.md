# Verification record

This document records the actual verification run, executed from a clean
working directory on Linux 6.8.0, Python 3.12.3.

## Environment

| Component | Version |
|---|---|
| Python | 3.12.3 |
| numpy | 2.4.6 |
| scipy | 1.15.3 |
| fastapi | 0.141.1 |
| pydantic | 2.13.5 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |

Install: `pip install -r requirements.txt` (all packages were already present
in the verification environment; no extra system services are required).

## Reproduction commands (from a clean checkout)

```bash
# 1. unit/integration/API tests with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

# 2. live service walkthrough (two terminals)
RCT_DB_PATH=/tmp/rct.sqlite3 RCT_LOG_PATH=/tmp/rct.log \
  python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
BASE=http://127.0.0.1:8000 bash examples/run_examples.sh
```

## Result of the test command — recorded as run

```
109 passed, 1 warning in ~14 s
```

(The single warning is Starlette's third-party deprecation notice about the
`httpx`-based `TestClient`; it originates outside this project.)

Coverage of the application package (statement coverage):

| Module | Cover |
|---|---|
| app/core/contract.py | 97% |
| app/core/kernel.py | 96% |
| app/core/intervals.py | 88% |
| app/evidence.py | 88% |
| app/diagnostics.py | 90% |
| app/storage.py | 98% |
| app/service.py | 93% |
| app/api.py | 97% |
| app/config.py | 100% |
| app/repro/fixtures.py | 88% |
| app/repro/replay.py | 96% |
| app/main.py | 0% (thin uvicorn launcher, exercised manually in step 2) |
| **TOTAL** | **93%** |

## What is asserted concretely (not "endpoint is callable")

* **Paired design.** The enumerated randomization set equals exactly
  `{-1,+1}^n` as a set, has size `2^n` (asserted ≠ `(2n)!`), and every vector
  is a collection of independent per-pair coins — units never cross pairs.
* **Exact zero-effect p-values — hand counts**: identical outcomes
  (`d=(0,0,0,0)`) → `p=1`; `d=(5,1,1,1)` → `p=2/16`; `d=(1,2,3,4,5)` →
  `p=2/32`; balanced `d=(1,-1,1)` → `p=1`; extreme alternating
  `d=(5,-5,5,-5)` → `p=1` at τ=0 and `8/16` at τ=10 (two sign-invariant zero
  residuals). Each fixture reference p-value is independently recomputed by
  the `itertools` oracle.
* **Exact inversion — endpoints**: `(1,-1,1)` @0.5 → `[-1,1]`; `(1,2,3)` @0.5
  → `[1,3]`, @0.75 → `[1.5,2.5]`; `(1,1,2,2)` @0.25 → `[1,2]`;
  `(3,-1,2,2)` @0.5 → `[0.5,2.5]`; `(1,-2,3,-4,5)` @0.1 → `[-4,5]`;
  identical outcomes @0.05 → whole real line (`UNBOUNDED_SET`), @0.5 →
  singleton `{0}`. Breakpoint and far-tail membership was additionally
  re-verified against an independent enumeration over randomized datasets and
  multiple α levels — **0 membership mismatches**.
* **Disconnected sets are never flattened**: a mask with rejected gaps yields
  3 singleton components; a two-cluster mask yields exactly two intervals with
  gap points rejected (`tests/test_inversion.py`).
* **Budget behavior**: `n=10` → exact; `n=20/30` → Monte-Carlo with
  `BUDGET_EXCEEDED` + `MONTE_CARLO_ERROR` uncertainties, positive SE and a
  reported 95% half-width; `n=6, 200k` MC draws converge to the exact p within
  tolerance.
* **Approximate replay**: same-seeded replay reproduces p-values, grid
  profiles and confidence sets bit-for-bit (`max_abs_difference = 0`); the
  replay bundle records seed, draw count, grid, environment and a SHA-256
  fingerprint.
* **Failure categories over HTTP (422/404)**: `TOO_FEW_PAIRS`,
  `INVALID_PAIR_SHAPE`, `MALFORMED_DATA` (invalid JSON and non-object body),
  `MISSING_DATA`, `NON_NUMERIC_OUTCOME`, `NON_FINITE_OUTCOME`,
  `INVALID_ALPHA`, `INVALID_INTEGER`, `INVALID_GRID`, `INVALID_N_DRAWS`,
  `UNKNOWN_REQUEST`.
* **Correlation/diagnostics**: response `request_id` matches the
  `X-Request-ID` header (client-supplied id is honored), results persist to
  SQLite and are retrievable by id, and every processing step is written to
  the JSON-lines log with request id, version and source location.

## Result of the live walkthrough — recorded values

Against `uvicorn app.api:app` (recorded run used a free local port; values
are seed/input determined):

| Step | Recorded result |
|---|---|
| `GET /health` | `{"status":"ok","service":"paired-rct-inference","version":"1.0.0"}` |
| exact p-value `d=(5,1,1,1), τ=0` | `p=0.125`, `count_as_extreme=2/16`, oracle cross-check `ok=true` |
| exact 90% set `d=(1,-2,3,-4,5)` | single interval `[-4.0, 5.0]`, oracle probe `discrepancies=[]` |
| MC p-value, 30 pairs, `M=10000`, seed `20260928` | method `monte-carlo-signflip`, `p=0.726527`, SE `0.004457`, 95% half-width `0.008736` |
| replay (`large_for_monte_carlo`, `M=2000`) | `reproducible=true`, `max_abs_difference=0.0`, fingerprint present |
| persistence by `walkthrough-001` | request `completed`, analyses retrievable from SQLite |

## Independence of reference answers

The kernel (`app/core/kernel.py`) is never used to generate the expected
answers it is tested against. They come from:

1. hand counting encoded in `app/repro/fixtures.py`, and
2. `app/evidence.py`, a separate plain-Python enumeration over
   `itertools.product((-1, 1), repeat=n)` that imports no kernel symbols.

The fixture hand counts are themselves asserted against the oracle in
`tests/test_fixture_references.py`, so a typo in a reference table fails the
build rather than being silently "confirmed" by the code under test.

## Known property (documented, not hidden)

For the chosen two-sided absolute-signed-sum statistic every assignment's
contribution interval contains the observed mean point `mean(d)` where
`p=1`; the exact and coupled-MC acceptance sets are therefore structurally a
single interval (the whole line when α is below the attainable minimum). The
engine nevertheless represents every result as an interval **set**, enumerates
components from breakpoints/grid masks without bridging rejected points, and
labels unbounded results as `UNBOUNDED_SET`. See README §"A structural note on
connectivity".
