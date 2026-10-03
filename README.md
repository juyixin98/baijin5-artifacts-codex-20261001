# seamcarve — minimal-energy seam backend with protected-region constraints

Synthetic-image seam carving backend: finds minimal-energy vertical seams
with a dynamic-programming kernel, enforces protected regions, and removes
seams as chunked jobs while reporting paths in **original image
coordinates**. Ships with a FastAPI validation interface, deterministic
fixtures and a fully self-checking test suite.

## Layout

```
seamcarve/
  contracts.py   image data contract: pixel/mask validation, result types
  energy.py      gradient energy AND forward energy (kept separate)
  kernel.py      numeric core: DP seam search, MAX_STEP, tie-break rules
  carving.py     seam removal + original-coordinate mapping (immutable states)
  jobs.py        chunked carve jobs with progress + run-identity logging
  api.py         FastAPI validation interface (/v1/seam/find, /v1/carve)
  config.py      independent configuration (env: SEAMCARVE_*)
  runlog.py      structured JSON-lines run logging
fixtures/        deterministic synthetic data generator + generated data
tests/           unit / exhaustive / API tests (references are hand-derived
                 or brute-force oracles living in the test files)
examples/        curl + python service-call examples
artifacts/       reviewable run results (test-run.log, smoke outputs)
```

## Algorithm contract

* **Displacement limit**: a seam moves at most `kernel.MAX_STEP = 1` column
  between adjacent rows.
* **Gradient energy** (ordinary): `|dx| + |dy|`, central differences with
  clamped borders, summed over channels. DP:
  `cost[i,j] = energy[i,j] + min(cost[i-1, j-1..j+1])`, base row = energy row 0.
* **Forward energy** (Avidan–Shamir): separate recurrence over the
  CU/CL/CR component matrices, base row = 0. Never mixed with gradient mode.
* **Protection**: protected pixels get `+inf` accumulated cost and can never
  be on a seam. If no finite-cost seam exists (e.g. a fully protected row),
  the kernel raises `NoLegalSeamError` and the API returns
  `409 NO_LEGAL_SEAM` — never a success.
* **Tie-breaking** (deterministic): predecessor preference straight >
  up-left > up-right; final-column ties break leftmost.
* **Coordinate mapping**: each removal updates `col_map` (current column ->
  original column, per row); energy is recomputed from the current image
  before every removal — stale energy is never reused.

## Error categories

| category | HTTP | meaning |
|---|---|---|
| `CONTRACT_VIOLATION` | 422 | malformed pixels/mask, bad seam count |
| `NO_LEGAL_SEAM` | 409 | protection blocks every legal seam |
| `REQUEST_VALIDATION` | 422 | schema-level rejection (e.g. bad energy_mode) |
| `INTERNAL_ERROR` | 500 | unexpected; carries run_id for log correlation |

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt                       # locked versions

python3 fixtures/generate_fixtures.py                 # regenerate fixtures (idempotent)
python3 -m pytest                                     # 60 tests; writes artifacts/test-run.log

uvicorn seamcarve.api:app --port 8000                 # serve
python3 examples/call_service.py                      # or see examples/curl_examples.md
```

## Verification artefacts

* `artifacts/test-run.log` — structured JSON-lines log of the pytest run:
  run id, component versions, per-test input hashes, asserted decision
  basis and outcomes.
* `artifacts/smoke/` — recorded responses of a real server run (normal
  find/carve plus the 409 no-legal-seam path).
* `tests/test_kernel.py` — brute-force seam enumeration oracle proves the
  kernel optimal on small images; `tests/test_carving.py` rebuilds
  intermediate images independently to prove energy recomputation and
  coordinate mapping after consecutive removals.
