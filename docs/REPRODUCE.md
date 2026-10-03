# Reproduction guide

Environment used for all recorded results below:

- Python 3.12.3 (Linux x86_64)
- numpy 2.4.6, scipy 1.15.3, fastapi 0.141.1, uvicorn 0.54.0,
  pydantic 2.13.5, pytest 9.1.1, pytest-cov 7.1.0, httpx 0.28.1
  (exact pins in `requirements.txt`)

## 1. Install

```bash
python3 -m venv .venv && . .venv/bin/activate   # optional
pip install -r requirements.txt
```

## 2. Fixtures

All data is synthetic and local. Regenerate deterministically (fixed seed
`20260927`; re-running produces byte-identical files):

```bash
python fixtures/generate_fixtures.py
```

| fixture | purpose |
|---|---|
| `short_impulse` | 0.95 impulse at sample 1000 → latency, anticipation, recovery |
| `stereo_imbalance` | L=0.9 / R=0.18 → linked gain preserves ratio 0.2 |
| `sustained_peaks` | continuous 0.95 sine → steady-state gain, permanent ceiling |
| `block_boundary_burst` | 0.9 burst across the 512 block edge → chunk-independent gain |
| `gain_recovery` | impulse + silence → release time constant |
| `true_peak_rich` | fs/4 sine, phase pi/4 → sample peak 0.389 under threshold, true peak 0.55 over |

## 3. Tests

```bash
python -m pytest            # or: python -m pytest -v --cov-report=term-missing
```

Recorded result (this repo state): **52 passed**, coverage **95.7%**
(gate: `--cov-fail-under=80`).

What the suite proves, mapped to the requirements:

- **Latency**: impulse at k appears at k+L uncompensated, at k after
  compensation (`tests/test_stream.py::TestLatency`).
- **Flush**: impulse 3 samples before the end survives; total emitted =
  consumed + L (`TestFlush`).
- **Block independence**: identical output for block sizes
  1/7/256/512/1024/4096 (`TestBlockSizeIndependence`).
- **Ceiling**: sustained, block-boundary burst and stereo-imbalance
  outputs never exceed `T*(1+1e-9)`; true-peak mode keeps the 4x
  oversampled output within T+0.1 dB (`tests/test_peak_ceiling.py`).
- **Gain recovery**: measured recovery time to 0.99 matches the analytic
  `tau_rel * fs * ln(0.99/g_min)` within 2 samples
  (`TestGainRecovery::test_release_time_constant`).
- **No hard clipping**: gain slew bounded by attack/release coefficients;
  the only fast drops are bounded, rare window-edge entries
  (`tests/test_envelope.py`).
- **Independent reference**: `tests/reference_impl.py` (plain loops, no
  shared code) matches the core to 1e-12 on all fixtures and seeded random
  signals (`tests/test_reference.py`).
- **Errors are not successes**: empty/ragged/non-finite input → 400,
  missing body → 422, invalid config → 400
  (`tests/test_api.py::TestErrorCategories`).

## 4. Service call example

In-process (no network), writes `docs/service_example_output.json`:

```bash
PYTHONPATH=src python3 scripts/service_example.py
```

Recorded output (`sustained_peaks`, default config):

```json
{
  "input_peak": 0.95,
  "output_peak": 0.5011872336272722,
  "threshold_linear": 0.5011872336272722,
  "min_gain": 0.5275655090813393,
  "latency_samples": 16,
  "sample_peak_ceiling_ok": true
}
```

Real HTTP run (also verified):

```bash
PYTHONPATH=src python3 -m uvicorn limiter.api:app --port 8791 &
curl -s localhost:8791/health
# -> {"status":"ok","versions":{"python":"3.12.3","numpy":"2.4.6","scipy":"1.15.3"}}
# POST fixtures/short_impulse.npz -> impulse stays at index 1000 after
# compensation, output_peak == threshold, ceiling_ok == true
```

## 5. Reading logs when a run fails

Every processing run emits JSON records on the `limiter.run` logger:

```
limiter {"run_id":"log-run","step":"run_start","versions":{...},"config":{...},...}
limiter {"run_id":"log-run","step":"api_request","input_sha256":"...","n_samples":2048,...}
limiter {"run_id":"log-run","step":"block","block_index":0,"n_emit":2048,"block_min_gain":0.527,...}
limiter {"run_id":"log-run","step":"run_end","max_output_peak":0.5012,"sample_peak_ceiling_ok":true,...}
```

To reproduce a failure: take the `run_id`, find `input_sha256` and
`config` in its `run_start`/`api_request` records, regenerate the same
input (fixtures are deterministic), and re-run with the same config. The
`run_end` record carries the verdict inputs (`max_output_peak` vs
`threshold_linear`), so the failing quantity is visible without
re-running. `tests/test_api.py::TestRunLogging` asserts these fields
exist.
