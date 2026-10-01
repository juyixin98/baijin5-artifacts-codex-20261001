# Paired Randomized Experiment — Randomization Test & Constant-Effect Inversion

A multi-module Python backend implementing, with no hard-coded demo shortcuts:

1. **Exact randomization tests** for *paired* randomized experiments, where the
   randomization set is the independent within-pair sign-flip set
   `{-1,+1}^n` (size `2^n`) — never an unrestricted shuffle of all units.
2. **Inversion for a constant (additive) treatment effect** using the *same
   two-sided statistic* as the test. The acceptance set is represented and
   returned as an ordered **union of disjoint intervals**; when a set is
   disconnected its gaps are preserved rather than collapsed to one interval.
3. **Honest approximation when enumeration is over budget**: if `2^n` exceeds
   the configured budget the service switches to a seeded Monte-Carlo
   sign-flip estimate and reports the method, draw count, standard error and a
   confidence half-width explicitly.

Everything runs locally with synthetic fixtures; no production accounts or
real participant data are needed.

---

## 1. Statistical contract

Data are paired outcomes. For pair `i`: treated outcome `t_i`, control
outcome `c_i`, observed difference `d_i = t_i - c_i`.

* **Design / randomization set.** Each pair has its own independent fair
  treatment coin. A randomization draw is a sign vector `s ∈ {-1,+1}^n`
  flipping which member of each pair is treated. There are exactly `2^n`
  assignments. Units never cross pair boundaries.
* **Sharp null of constant effect.** `H0(τ): Y(1) − Y(0) = τ` for every unit;
  residuals are `r_i(τ) = d_i − τ`.
* **Two-sided statistic (identical for testing and inversion):**

  ```
  T(s; τ) = | Σ_i s_i (d_i − τ) |,     T_obs(τ) = | Σ_i (d_i − τ) |
  p(τ)    = #{ s : T(s;τ) ≥ T_obs(τ) } / 2^n
  ```

  Ties are counted together with the observed assignment.
* **Confidence set by test inversion:** `C_α = { τ : p(τ) ≥ α }`, returned as
  a union of disjoint closed intervals.
* **Monte-Carlo (over budget):** `p̂ = (1 + X)/(1 + M)` with the observed
  assignment included, plus a binomial standard error and a normal 95%
  half-width. MC inversion is evaluated on an explicit grid and labels its
  endpoints with both grid-step and MC error.

### A structural note on connectivity (read before assuming gaps)

For this particular two-sided statistic every sign vector's contribution set
`{τ : T(s;τ) ≥ T_obs(τ)}` contains the observed mean point `τ̄ = mean(d)`
(the observed residual sum is zero there, so `p(τ̄) = 1`). Hence the exact
acceptance set — and the coupled Monte-Carlo acceptance set — is star-shaped
about `τ̄` and therefore a single interval. The implementation never assumes
this: results are always interval *sets*, the machinery enumerates disjoint
components and preserves gaps (`tests/test_inversion.py` proves disconnected
sets survive). Alternate statistics or independent-per-τ resampling can
produce multiple components, which the API would return unchanged. With very
small α the interval is the whole real line; this **unbounded weak-test
artifact is reported as an uncertainty (`UNBOUNDED_SET`), not silently
converted to a finite number.

---

## 2. Module layout

```
app/
  config.py            # env-driven configuration
  core/
    contract.py        # statistical contract: validation, types, statistic
    kernel.py          # exact sign-flip enumeration, p-values, exact
                       #   breakpoint-sweep inversion, MC engine + MC inversion
    intervals.py       # interval / interval-set algebra (gaps preserved)
  evidence.py          # INDEPENDENT itertools oracle + cross-checks and the
                       #   failure/uncertainty diagnostic summary
  diagnostics.py       # request-scoped JSON-lines logging (request_id,
                       #   version, step, processing location)
  storage.py           # SQLite persistence of requests and analyses
  repro/
    fixtures.py        # small synthetic datasets with hand/independent
                       #   reference answers
    replay.py          # run-twice deterministic replay bundle + fingerprint
  service.py           # orchestration: budget decision, evidence, persistence
  api.py               # FastAPI wiring (pvalue / inversion / replay / …)
  main.py              # uvicorn entry point
tests/                 # independent assertions on concrete values & failures
examples/              # request JSON samples and a curl walkthrough
```

Statistical contract, estimation kernel, evidence/diagnostics and
reproducibility are separate modules with distinct responsibilities.

---

## 3. Setup and dependencies

Requires Python 3.12 (3.10+ should work). Pinned, verified versions:

| Package | Version |
|---|---|
| numpy | 2.4.6 |
| scipy | 1.15.3 |
| fastapi | 0.141.1 |
| pydantic | 2.13.5 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `RCT_EXACT_BUDGET_FLIPS` | `100000` | exact enumeration allowed while `2^n ≤ budget` |
| `RCT_MC_DRAWS` | `10000` | default Monte-Carlo draw count |
| `RCT_MC_ERROR_CONFIDENCE` | `0.95` | confidence level of the MC error band |
| `RCT_GRID_HALF_WIDTH` | `10.0` | MC inversion grid half-width around `mean(d)` |
| `RCT_GRID_POINTS` | `4001` | MC inversion grid points (odd) |
| `RCT_DB_PATH` | `data/rct.sqlite3` | SQLite database path |
| `RCT_LOG_PATH` | `data/app.log` | JSON-lines log path |
| `RCT_LOG_LEVEL` | `INFO` | log level |

---

## 4. Run the service

```bash
python -m app.main
# or: uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/` (contract summary) and
`http://127.0.0.1:8000/docs` (OpenAPI). Every response carries
`X-Request-ID` and `X-Service-Version` headers; supply your own correlation id
with the `X-Request-ID` header.

---

## 5. Request examples

A self-contained curl walkthrough lives in
[`examples/run_examples.sh`](examples/run_examples.sh); sample bodies are in
`examples/*.json`.

```bash
# Exact test, zero effect, d = (5,1,1,1)  → p = 2/16
curl -s -X POST localhost:8000/api/v1/pvalue \
  -H 'Content-Type: application/json' \
  -d @examples/pvalue_exact.json

# Same question with explicit [treated, control] pairs and τ = 0
curl -s -X POST localhost:8000/api/v1/pvalue \
  -H 'Content-Type: application/json' \
  -d '{"pairs": [[5,0],[1,0],[1,0],[1,0]], "effect": 0}'

# Exact 90% constant-effect confidence set, d = (1,-2,3,-4,5) → [-4, 5]
curl -s -X POST localhost:8000/api/v1/inversion \
  -H 'Content-Type: application/json' \
  -d @examples/inversion_exact.json

# 30 pairs ⇒ 2^30 > budget ⇒ Monte-Carlo with reported error
curl -s -X POST localhost:8000/api/v1/pvalue \
  -H 'Content-Type: application/json' \
  -d @examples/pvalue_monte_carlo.json

# Deterministic replay (runs the MC analysis twice with the same seed)
curl -s -X POST localhost:8000/api/v1/replay \
  -H 'Content-Type: application/json' \
  -d @examples/replay.json

# Fixture catalogue (with independent reference answers)
curl -s localhost:8000/api/v1/fixtures

# Replay the persisted request/result by correlation id
curl -s localhost:8000/api/v1/requests/<request_id>
```

Exact responses embed an `independent_cross_check`: p-values and acceptance
membership are recomputed by the independent plain-Python `itertools` oracle
in `app/evidence.py` (which does **not** import the kernel).

### Reading the response envelope

* `statistical_contract` — design, `2^n` size, exact statistic definition.
* `result` — method, statistic, p-value/count or `confidence_set`, budget
  decision, and (approximate path) MC error fields.
* `result.summary.failures` vs `result.summary.uncertainties` — hard failures
  and approximate/unbounded conclusions are listed separately.
* `diagnostics.processing_steps` — ordered `step` / `location` / `level`.
* The same records are appended as JSON lines to `$RCT_LOG_PATH`.

### Failure codes

`MISSING_DATA`, `AMBIGUOUS_DATA`, `MALFORMED_DATA`, `INVALID_PAIR_SHAPE`,
`NON_NUMERIC_OUTCOME`, `NON_FINITE_OUTCOME`, `TOO_FEW_PAIRS`, `INVALID_ALPHA`,
`INVALID_INTEGER`, `INVALID_GRID`, `INVALID_N_DRAWS`, `UNKNOWN_REQUEST`,
`REPLAY_MISMATCH`, `INTERNAL_ERROR`.

---

## 6. Test command

```bash
python -m pytest tests/ --cov=app --cov-report=term-missing
```

The suite asserts **concrete numerical results and concrete failure
categories**, not mere callability. Expected answers come from hand counts
(`app/repro/fixtures.py` reference tables, cross-checked by the oracle) and
from the independent `itertools` enumeration — never from the kernel under
test. It covers: identical outcomes, extreme/balanced differences, exact
breakpoint membership across many datasets and α levels, disconnected-set
preservation, budget switching, MC error reporting and convergence, seeded
replay bit-for-bit reproducibility, HTTP failure categories, persistence
correlation and the unbounded-set uncertainty.

Recorded results and dependency versions are in
[VERIFICATION.md](VERIFICATION.md).
