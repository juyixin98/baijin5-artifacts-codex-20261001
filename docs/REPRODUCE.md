# Reproduction

## Environment

- Python 3.12 (>= 3.11 works)
- Pinned dependencies: `requirements.txt`
  (numpy 2.4.6, scipy 1.15.3, fastapi 0.141.1, uvicorn 0.54.0,
  pydantic 2.13.5, httpx 0.28.1, pytest 9.1.1)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run the tests (normal + abnormal cases)

```bash
python3 -m pytest            # 20 passed
```

Abnormal cases are part of the suite: NaN/Inf blocks, wrong channel
counts, ragged blocks, illegal config, unknown session, use-after-flush —
each asserts its specific error category and HTTP status, never a 200.

## Reviewable artifacts

Every run writes new files under `results/` (kept in the repo):

- `run-<id>.jsonl` — one JSON object per test step: run id, environment
  fingerprint (Python/NumPy/SciPy/FastAPI versions, git sha), fixture
  sha256, config, computed metrics, thresholds, and the verdict. To
  correlate a failure with its input, join `fixture_sha256` against
  `fixtures/manifest.json`.
- `demo-<fixture>-<id>.json` — offline demo reports.

## Regenerate fixtures

```bash
python3 scripts/make_fixtures.py   # deterministic; hashes in fixtures/manifest.json
```

## Run the service demo

```bash
uvicorn limiter.api:app --port 8000 &
python3 examples/api_demo.py impulse
```

Expected output (measured 2026-10-03, see `results/`):

- `/v1/limit` on the impulse fixture: `output_peak 0.5000653207009724`,
  `promised_ceiling 0.5000653207009725`, `ceiling_ok: true`,
  `gain_min ≈ 0.25003` (threshold/amplitude = 0.25)
- streaming session over 4096 frames: emitted `[1808, 2048, 240]`,
  sum = 4096 = input length (240 = lookahead latency, flushed at the end)
- NaN payload → `422 {"error": {"category": "non_finite", …}}`

## Offline demo

```bash
python3 scripts/offline_demo.py impulse
python3 scripts/offline_demo.py sustained_sine
```
