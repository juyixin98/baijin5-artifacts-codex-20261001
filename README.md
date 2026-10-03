# Allelic Haplotype Assembly (synthetic diploid phasing backend)

Backend service that phases synthetic variant sites into two diploid
haplotypes from read-level allele support, using an exact Minimum Error
Correction (MEC) objective. All inputs are local synthetic fixtures or
inline JSON — no external accounts or real data.

Stack: Python 3.12 · FastAPI · NumPy · SQLite (provenance) · pytest.

## Layout

```
app/
  parsing.py     dataset validation + normalization to dense arrays
  phasing.py     block detection + exact MEC enumeration + evidence
  provenance.py  SQLite run store (request id, versions, input hash)
  service.py     orchestration: validate -> phase -> record -> respond
  main.py        FastAPI app and routes
  config.py      settings (config/default.json, env overrides)
  errors.py      failure categories shared across layers
fixtures/        synthetic datasets (see docs/expected_results.md)
tests/           independent tests (hand-computed + brute-force oracle)
scripts/         run_fixture.py — run a fixture without a server
docs/            hand-computed reference arithmetic
config/          default runtime configuration
```

## Domain rules

- **Allele encoding**: `ref = 0`, `alt = 1`; calls may also be `unknown`
  (ignored by the objective, surfaced as a warning).
- **Quality-to-cost rule (fixed)**: explaining a read by a haplotype
  costs the sum of phred qualities of the calls that disagree with it;
  matches cost 0.
- **Objective**: `MEC(h1) = Σ_reads min(cost(read, h1), cost(read, h2))`
  with `h2 = 1 − h1` (sites assumed heterozygous), minimized by
  enumerating all `2^(k−1)` candidates per block.
- **Flip equivalence**: swapping the two haplotypes of a block is the
  same solution; the canonical orientation pins the block's first site
  to its ref allele on haplotype 1.
- **Blocks**: sites co-covered by at least one read form connected
  components. Each component is phased independently and reported as a
  separate block; no phase relation is claimed across blocks.
- **Ambiguity**: all MEC-optimal phases are kept; more than one optimum
  flags the block as ambiguous and is listed under `uncertainties`.

## Failure categories

Validation and processing failures are categorized (HTTP 422):
`EMPTY_DATASET`, `DUPLICATE_SITE_ID`, `DUPLICATE_READ_ID`,
`DUPLICATE_CALL_IN_READ`, `INVALID_REFERENCE_ALLELE` (e.g. `N`),
`INVALID_ALT_ALLELE`, `QUALITY_OUT_OF_RANGE`,
`READ_REFERENCES_UNKNOWN_SITE`, `BLOCK_TOO_LARGE`,
`NO_INFORMATIVE_READS`.

## Setup

```bash
pip install -r requirements-dev.txt   # runtime + test deps, pinned
```

## Run the tests

```bash
python3 -m pytest tests/ -v
```

## Run the service

```bash
uvicorn app.main:app --port 8000
```

### Example calls

```bash
# Phase a bundled synthetic fixture
curl -s -X POST localhost:8000/v1/phase/fixture/error_reads | jq

# Phase an inline dataset
curl -s -X POST localhost:8000/v1/phase \
  -H 'Content-Type: application/json' \
  -d @fixtures/basic_clean.json | jq

# Look up the provenance record of a run (request_id from the response)
curl -s localhost:8000/v1/runs/<request_id> | jq

# Versions used to produce results
curl -s localhost:8000/v1/version | jq
```

Without a server:

```bash
python3 scripts/run_fixture.py ambiguous
```

## Response shape

A successful `/v1/phase` response contains:

- `request_id` — UUID correlating the response, the logs, and the
  provenance record;
- `versions` — app and algorithm versions;
- `input_sha256` — hash of the exact request payload;
- `result.blocks[]` — per block: `site_ids`, `haplotype1`/`haplotype2`
  (base strings), `mec`, `ambiguous`, `n_optima`, `alternative_optima`,
  `supporting_reads`/`conflicting_reads`, and per-read
  `read_assignments` with correction details (site, alleles, qual);
- `result.warnings` — e.g. ignored unknown-allele calls;
- `result.uncertainties` — ambiguous blocks, singleton sites,
  unsupported blocks.

Failures return `{"request_id, status: "failed", error: {category,
message, context}}` and are also recorded in the provenance store.

## Provenance

Every request (success or failure) is appended to a SQLite store
(default `data/provenance.db`, override with `HAPLO_PROVENANCE_DB` or
`config/default.json`) with request id, UTC timestamp, sample id, input
SHA-256, app/algorithm/NumPy versions, status, and the full result or
categorized error. `GET /v1/runs/{request_id}` retrieves a record.

## Configuration

`config/default.json`:

| key | default | meaning |
|-----|---------|---------|
| `max_quality` | 60 | phred qualities above this are rejected |
| `max_enum_sites` | 20 | blocks larger than this are refused (exact enumeration) |
| `provenance_db_path` | `data/provenance.db` | SQLite store location |
| `fixtures_dir` | `fixtures` | bundled fixture directory |

`HAPLO_CONFIG=/path/to/override.json` replaces individual keys.

## Verification status

`python3 -m pytest tests/` — all tests pass (validation categories,
hand-computed fixture results, flip equivalence, disconnected blocks,
independent brute-force oracle cross-check on 30 random instances, API
and provenance round-trips). See the session log for the exact run.

## Remaining limitations

- Sites are assumed heterozygous; homozygous sites are not modeled
  (`h2 = 1 − h1`).
- Exact enumeration caps blocks at `max_enum_sites` (default 20) sites;
  larger blocks are refused with `BLOCK_TOO_LARGE` rather than
  approximated.
- The cost rule is linear in phred quality; no indel/realignment model,
  no base-quality recalibration, no ploidy beyond diploid.
- Indels and multi-allelic sites are out of scope (single ref/alt SNVs
  only).
- The provenance store is a single-writer SQLite file; no retention or
  compaction policy.
