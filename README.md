# wsola-backend

Audio-only WSOLA (Waveform Similarity Overlap-Add) time-stretch backend.
Python + FastAPI + NumPy/SciPy. All verification runs on local synthetic
fixtures — no production accounts, no external data.

## Layout

```
wsola_backend/
  config.py       fixed algorithm parameters + supported rate ranges
  contracts.py    Pydantic request/response schemas (the sample contract)
  wsola.py        offline WSOLA core (pure NumPy/SciPy, whole-signal)
  stream.py       WsolaStream: chunked processing, bit-identical to offline
  diagnostics.py  request ids, decision records, masked logging
  fixtures.py     synthetic signals: tone / impulse train / silence / noise
  metrics.py      independent measurement helpers (FFT peak, seam, SNR)
  service.py      validation + accept/reject/undecidable decisions
  main.py         FastAPI app: POST /v1/time-stretch, GET /healthz
tests/            pytest suite (contracts, offsets, length, quality, stream, api)
scripts/verify.py verification battery; exit 1 on any failed check
requirements.txt  pinned dependencies
```

## Contract and boundary semantics

**Rate.** `output_length = round(input_length / rate)`. `rate > 1` is
faster (shorter output), `rate < 1` is slower (longer output).

**Supported range.** Hard limits `[0.25, 4.0]`: outside -> HTTP 422
`RATE_OUT_OF_RANGE`. Guaranteed-quality range `[0.5, 2.0]`: inside hard
limits but outside quality range -> accepted with a
`RATE_OUTSIDE_QUALITY_RANGE` warning; artifacts are expected there.

**Hops and window (fixed).** Synthesis hop `Hs = 512`, window `L = 1024`
(periodic Hann, 50% overlap, exact COLA). Analysis hop `Ha = rate * Hs`.
The nominal position of segment `k` is `round(k * Ha)`: per-segment
rounding keeps position drift `<= 0.5` sample, never accumulating.

**Local match (fixed).** For each segment, normalized cross-correlation
between the synthesized overlap and each candidate frame, both weighted
by the overlap half-window, over `delta in [-256, +256]` clamped to keep
the window inside the input.

**Tie-break (deterministic).** Candidates scan in the fixed order
`0, -1, +1, -2, +2, ...`; a candidate wins only on a strictly greater
score. Silence/DC makes the score degenerate (`0.0` everywhere), so
`delta = clamp(0, lo, hi)` wins — silence and periodic inputs with
multiple optima resolve deterministically and are flagged
`degenerate=true`.

**Tail pinning.** For strong slow-down the ideal analysis trajectory ends
beyond the last placeable frame start `n - L`. Those tail segments search
the last `D`-wide range of placeable starts instead (flagged
`pinned=true`), so the tail is phase-aligned content — never zero
padding. Every frame is always placed, so the output always covers the
target length.

**End rule.** The frame count covers at least the target length, so the
final output is exactly `round(n / rate)` samples via `trim` (or `exact`
when nothing is trimmed). `end_compensation_samples <= 0` reports how
many samples were trimmed.

**Minimum input.** One window (`1024` samples). Shorter input -> HTTP 422
`INPUT_TOO_SHORT`.

**No artifact-free guarantee.** WSOLA aligns waveform similarity, not
perception. Outside `[0.5, 2.0]`, or on highly non-stationary content,
audible artifacts are expected. Distortion is *reported* (per-case SNR in
`scripts/verify.py`), not promised away.

## Per-segment offsets

Every response carries `segments[]`: `ideal_position`, `nominal_position`,
`delta` (the match offset), `analysis_position`, `correlation`,
`degenerate`, `pinned`. Segment 0 always has `delta = 0`,
`correlation = null` (no overlap exists yet).

## Diagnostics

Every request gets a `request_id` (client-supplied or generated) and a
`diagnostics[]` trail explaining each decision with key state:
`REQUEST_RECEIVED` (length + truncated SHA-256 fingerprint, never raw
samples), `RATE_OUTSIDE_QUALITY_RANGE`, `DEGENERATE_MATCH` (undecidable
-> deterministic rule), `TAIL_PINNED`, `END_COMPENSATION`,
`REQUEST_ACCEPTED`. Rejections return HTTP 422 with the same records in
`detail`. Sensitive audio content is never logged — only lengths and
fingerprints.

## Run

```bash
pip install -r requirements.txt
python -m pytest tests/        # 97 tests
python scripts/verify.py       # verification battery, exit 1 on failure
uvicorn wsola_backend.main:app # serve
```

Example:

```bash
curl -X POST localhost:8000/v1/time-stretch -H 'content-type: application/json' -d '{
  "sample_rate": 16000, "rate": 1.5,
  "samples": [0.0, 0.1, "..."]
}'
```

## Verification coverage

`scripts/verify.py` checks, against fixture parameters and independent
measurements (never the core's own output as its own reference):
exact output length, seam continuity, dominant-frequency preservation,
sinusoid-fit SNR (distortion), impulse period preservation + count
scaling, silence stays silent, and streaming/offline bit-equality.

**NOT RUN (explicitly not claimed as passed):**

- perceptual listening evaluation (MUSHRA/ABX) — needs human listeners;
- real-world audio corpus regression — policy is local fixtures only;
- long-duration soak (>10M samples via HTTP) — covered at unit level by
  the streaming/offline equivalence tests instead.
