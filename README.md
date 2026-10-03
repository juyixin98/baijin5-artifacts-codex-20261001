# miniseed — minimizer seed index & candidate-location lookup

A small, fully local service that indexes a synthetic DNA reference with
**minimizer seeds** and answers candidate genomic locations for synthetic
reads. The stack is Python + FastAPI + NumPy + SQLite. There are no external
services, accounts, or real biological data — all inputs are local synthetic
fixtures.

> **Important semantic:** query results are **candidate seed hits**, not
> alignments and not variant/match conclusions. A hit means the read and the
> reference share a minimizer value; downstream extension/alignment is
> required to confirm a mapping.

## Algorithm contract (fixed & versioned)

1. **Parameters / hash / ties**
   - `k` = k-mer length, `w` = window length in k-mers; both positive,
     `k <= 31`. A sequence needs length `>= k + w - 1` to cover one window.
   - Hash: FNV-1a over a fixed two-bit encoding (`A=0,C=1,G=2,T=3`),
     reduced modulo `2**63`; version string `fnv1a-2bit-v1`.
   - **Tie rule:** when k-mers hash equally, the **leftmost offset** in the
     window wins (deterministic).
   - **Canonical normalization:** each k-mer is reduced to the
     lexicographically smaller of itself and its reverse complement. Seeding
     is therefore strand-agnostic, while each record retains the orientation
     (`+`/`-`) so the read strand is preserved.
2. **Deduplication & low-complexity guard**
   - A minimizer is emitted once per maximal run of **adjacent windows that
     select the same value** (first-occurrence dedup by value). A homopolymer
     yields one seed; a value leaving the window and returning later starts a
     new record; an all-`N` window breaks adjacency.
   - A `(run, minimizer)` hash bucket is capped
     (`MINISEED_MAX_BUCKET`, default 200). Tandem-repeat/low-complexity
     references that would exceed it fail indexing with `BUCKET_OVERFLOW`
     instead of exploding candidate counts.
3. **Candidate hits are not alignments.** Responses carry
   `candidate_is_alignment: false` and group hits into candidate locations by
   strand-aware diagonal (`ref - query` forward, `ref + query` reverse).

## Project layout

```
src/miniseed/
  config.py       # configuration layer (env overrides, no magic numbers)
  errors.py       # explicit error taxonomy (stable codes) + HTTP mapping
  sequence.py     # synthetic parsing, validation, FASTA, canonical k-mers
  hashing.py      # versioned fixed FNV-1a two-bit hash
  minimizer.py    # domain core: sliding-window minimizer + dedup
  store.py        # SQLite repository: runs / seeds / audit + bucket cap
  service.py      # orchestration + NumPy diagonal candidate grouping
  schemas.py      # Pydantic API models
  api.py          # FastAPI verification interface
  logging_setup.py# structured provenance logging
scripts/
  serve.py        # runnable HTTP entry point
  demo.py         # local end-to-end demo over fixtures
fixtures/         # synthetic reference + reads (no real data)
tests/
  oracle.py       # INDEPENDENT brute-force reference (does not import core)
  test_*.py       # concrete-result + failure-category assertions
```

## Setup & reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate   # optional
pip install -r requirements.txt

# 1. Run the test suite (actually executes; reports pass/fail)
python3 -m pytest -q

# 2. Local end-to-end demo over the synthetic fixtures
python3 scripts/demo.py

# 3. Run the HTTP service
python3 scripts/serve.py            # http://127.0.0.1:8000  (docs at /docs)
```

### HTTP usage

```bash
# Index a synthetic reference
curl -s -X POST localhost:8000/api/v1/runs \
  -H 'content-type: application/json' \
  -d '{"run_id":"r1","reference":"ACGTACGT..."}'

# Query a synthetic read
curl -s -X POST localhost:8000/api/v1/query \
  -H 'content-type: application/json' \
  -d '{"run_id":"r1","read":"ACGTACGT..."}'

curl -s localhost:8000/health
curl -s localhost:8000/api/v1/runs/r1/audit
```

Every request/response carries a correlation id (`x-request-id` header; pass
your own to correlate a run). The same id appears in structured logs and in
the SQLite `audit` table.

## Error semantics

Failures never return a success envelope. The body is
`{"ok": false, "request_id": "...", "error": {"code", "message", "context"}}`.

| Code | HTTP | Meaning / judgment basis |
|------|------|--------------------------|
| `EMPTY_SEQUENCE` | 400 | null/whitespace-only input |
| `INVALID_CHARACTER` | 400 | base outside `ACGTN` (context names the character) |
| `SEQUENCE_TOO_SHORT` | 400 | shorter than `k + w - 1`; no window possible |
| `INVALID_PARAMETER` | 400 | non-positive `k`/`w`, or schema validation failure |
| `PARAMETER_CONFLICT` | 400 | query `(k,w)` differs from the indexed run's, or `k>31` for the hash version |
| `RUN_NOT_FOUND` | 404 | unknown run id |
| `RUN_ALREADY_EXISTS` | 409 | recreate without `overwrite:true` |
| `EMPTY_INDEX` | 409 | reference produced no seeds (e.g. all N) |
| `BUCKET_OVERFLOW` | 422 | a minimizer bucket exceeds the low-complexity cap |
| `TOO_MANY_CANDIDATES` | 422 | query exceeds the candidate cap |
| `INTERNAL_ERROR` | 500 | unexpected exception — logged with detail, never reported as success |

## Verification approach (why the tests are trustworthy)

- **Independent oracle** (`tests/oracle.py`) re-derives expected minimizers
  with a separately written brute-force **full-window enumeration** and its
  own hash; it never imports the implementation under test. Expected answers
  are therefore not generated by the core being validated.
- **Golden hash vectors** pin literal FNV-1a values for `A/C/T` and 9-mers.
- Required cases assert **concrete results**, not callability:
  same-value long run collapses to one seed; tail single-window read;
  reverse-complement read; exhaustive small-alphabet enumeration vs oracle;
  candidate recall at a known planted coordinate (`diagonal == 50`);
  parameter-incompatibility and each failure category.
- An end-to-end API test forces an unexpected exception and asserts
  `500 / INTERNAL_ERROR / ok=false`.

## Logging & provenance

Structured lines go to stderr and `logs/miniseed.log` (demo:
`logs/demo.log`), e.g.:

```
... INFO identity=bc3b.. run_id=demo-syn-001 step=index_done verdict=OK \
    windows=118 seeds=36 distinct=14 max_bucket=9
```

Startup logs Python/NumPy/hash versions; each index/query logs its step,
progress counters, and the judgment basis; errors log `verdict=ERROR` with
the stable code. The SQLite `audit` table stores per-run events keyed by the
correlation id.

## Configuration

Env vars (see `.env.example`): `MINISEED_K`, `MINISEED_W`,
`MINISEED_MAX_BUCKET`, `MINISEED_MAX_CANDIDATES`, `MINISEED_DB`.
Defaults: `k=9, w=5, max_bucket=200, max_candidates=500`.
