# Lookahead Limiter (streaming, synthetic PCM)

Streaming lookahead limiter for synthetic PCM with **latency compensation**
and **gain trajectory output**. Python + NumPy/SciPy core, FastAPI service,
pytest suite with an independent reference implementation.

## Algorithm contract (fixed)

All formulas below are the specification; `src/limiter/config.py` is the
normative statement and `tests/reference_impl.py` re-implements them
independently (plain Python loops, no shared code).

- **Peak detection / channel linking**: per-sample maximum of `|x|` across
  **all** channels. One gain trajectory drives every channel (full link),
  so inter-channel balance is preserved exactly.
- **Required gain**: `r[n] = min(1, T / peak[n])`, `T = 10^(threshold_dbfs/20)`.
- **Attack anticipation** (backward exponential ramp over the lookahead
  window `L`):
  `g1[n] = min_{0 <= j <= L} r[n+j] * attack_coeff^(-j)`
- **Release** (forward multiplicative recovery):
  `g[n] = min(g1[n], g[n-1] * release_coeff)`
- **Coefficients**: `attack_coeff = exp(-1000/(attack_ms*fs)) < 1`,
  `release_coeff = exp(+1000/(release_ms*fs)) > 1`.

### Ceiling semantics — sample peak vs true peak (explicit)

- **Default (`true_peak: false`)**: the threshold constrains the **sample
  peak**. The guarantee is structural, not empirical: `g[n] <= r[n]` for
  every `n`, hence `|out[n]| <= T` for every emitted sample. No per-sample
  hard clipping exists anywhere in the pipeline — the ceiling falls out of
  the envelope itself.
- **`true_peak: true`**: peaks are measured on the 4x (or 2x/8x)
  polyphase-oversampled waveform, so the threshold constrains the
  **reconstructed (inter-sample) true peak** up to a documented tolerance
  of **+0.1 dB** (polyphase filter ripple + base-rate gain quantization).
  Requires `lookahead_samples >= 32`.

### Window-edge entry (documented limitation)

If a required-gain drop is steeper than `attack_ms` can traverse within
`lookahead_ms` (`r[m] < g * attack_coeff^L`), the gain takes one bounded
step at the moment the peak enters the lookahead window. The ceiling still
holds; choose `attack_ms << lookahead_ms` to avoid the step. This is
asserted precisely in `tests/test_envelope.py`.

## Latency model

- Output stream = limited input delayed by exactly `L = lookahead_samples`.
- `process(block)` consumes B samples and emits B samples
  (`out[i] = x[i-L] * g[i-L]`, leading zeros while `i < L`).
- `flush()` emits the final L tail samples — **no tail sample is lost**.
  Total emitted = total consumed + L.
- **Delay compensation**: drop the first L samples of the concatenated
  output; `limit_offline()` does this and returns output aligned with input.
- The gain trajectory is returned per emitted sample, aligned with the
  emitted PCM.

## Layout

```
src/limiter/
  config.py     # parameter contract + validation (fixed formulas)
  envelope.py   # required gain, attack ramp, release pass (pure functions)
  peaks.py      # sample-peak / true-peak (oversampled) detectors
  stream.py     # LimiterStream: delay line, block state, flush
  offline.py    # whole-signal wrapper with delay compensation
  runlog.py     # structured per-run JSON logging (run id, versions, hashes)
  api.py        # FastAPI service
config/limiter.default.json
fixtures/       # deterministic synthetic PCM fixtures + generator
tests/          # unit / streaming / ceiling / reference / API tests
scripts/service_example.py
docs/REPRODUCE.md
```

## Quick start

```bash
pip install -r requirements.txt
python fixtures/generate_fixtures.py   # regenerate fixtures (deterministic)
python -m pytest                       # 52 tests, coverage gate 80%
PYTHONPATH=src python3 scripts/service_example.py
```

## Service

```bash
PYTHONPATH=src uvicorn limiter.api:app --port 8791
curl -s localhost:8791/health
curl -s -X POST localhost:8791/v1/limit -H 'content-type: application/json' -d '{
  "config": {"threshold_dbfs": -6.0, "attack_ms": 0.5, "release_ms": 50.0,
             "lookahead_ms": 2.0, "sample_rate": 8000.0},
  "channels": [[0.0, 0.9, 0.0], [0.0, 0.9, 0.0]],
  "run_id": "demo-1"
}'
```

Response: `run_id`, `latency_samples`, delay-compensated `output`
(channel-major), per-sample `gain`, and `stats` (peaks, min gain,
`sample_peak_ceiling_ok`). Invalid input returns 4xx with a reason;
unexpected failures return 500 with a run id — never a silent success.

## Observability

Every run logs structured JSON records (`limiter.run` logger) carrying
`run_id`, library versions, config, per-block input SHA-256, progress
counters, and the final verdict inputs (`max_output_peak` vs
`threshold_linear`). See `docs/REPRODUCE.md` for how to read them when a
test fails.
