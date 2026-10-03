# Graph-Cut Binary Segmentation Service

Two-label energy minimization on synthetic images via s-t min cut, exposed
as a FastAPI service. Every returned labeling carries an **energy
decomposition** and a **cut certificate** that proves (within float64
tolerance) that the labeling is a global optimum of the stated energy.

## Energy model

For a labeling `x ∈ {0,1}^N` (1 = foreground, 0 = background):

```
E(x) = Σ_p U_p(x_p)  +  Σ_{(p,q) ∈ N4} V(x_p, x_q)
```

- `U_p` — per-pixel data terms, **must be non-negative** (validated);
- `V` — a 2×2 pairwise table over 4-neighbour edges, **must be
  non-negative and submodular** (`v00 + v11 ≤ v01 + v10`). Non-submodular
  potentials are **rejected** (`NON_SUBMODULAR_POTENTIAL`) — the service
  never silently repairs them (no abs()/clamping);
- hard seeds are enforced with a **computed** big-M:
  `M = 1 + Σ(all non-seed arc capacities)`, which is strictly larger than
  any seed-respecting cut, and is checked against a float64-exactness
  headroom (`2**52` by default) so seed constraints cannot overflow.

The graph construction is Kolmogorov–Zabih: each pairwise term decomposes
into a constant, two unary shifts and two directed arcs of capacity
`k/2` (`k = v01 + v10 − v00 − v11 ≥ 0`); per-pixel unaries become t-links.
The constant is tracked explicitly, so the verified identity is

```
flow_value == cut_capacity                  (strong duality, arcs only)
cut_capacity + graph_constant == energy     (graph correspondence)
```

The max-flow kernel is a self-contained float64 Dinic implementation
(`graphcut/maxflow.py`) — SciPy's `maximum_flow` only accepts integer
capacities, which would break exact energy accounting.

## Layout

```
graphcut/
  errors.py       typed error taxonomy (5 categories, see below)
  config.py       env-based settings (GRAPHCUT_* prefix)
  contracts.py    input boundary: validated SegmentationSpec, seeds, N4 edges
  energy.py       independent energy evaluator (no shared code with graph)
  graph.py        s-t graph builder (chunked, big-M, overflow guard)
  maxflow.py      numerical kernel: float64 Dinic + residual reachability
  certificate.py  cut certificate: flow/cut/energy cross-check + seed check
  service.py      orchestration: spec -> graph -> solve -> certificate
  jobs.py         chunked background jobs (PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED)
  imaging.py      image data contract: array / png_base64 -> unaries
  schemas.py      pydantic request models -> contracts
  main.py         FastAPI app, endpoints, error-to-HTTP mapping
tests/
  unit/           contracts, graph correspondence, kernel, certificate, jobs
  integration/    hand-computed answers, brute-force enumeration, HTTP API,
                  committed sample data
scripts/make_sample_data.py   regenerates data/circle_64.png + sample_request.json
data/             committed synthetic fixtures
test_logs/        per-run test logs (run id, seeds, intermediate states)
```

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# regenerate sample fixtures (optional, they are committed)
python scripts/make_sample_data.py

# run the service
uvicorn graphcut.main:app --port 8000
# or: python -m graphcut.main
```

Segment the committed sample (64×64 noisy circle with hard seeds):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/segment \
  -H 'Content-Type: application/json' -d @data/sample_request.json
```

Real output (abridged):

```json
{
  "run_id": "58e52921fece",
  "energy": {"data": 1021.1489, "smooth": 222.0, "total": 1243.1489,
             "graph_constant": 1021.1489},
  "certificate": {"flow_value": 222.0, "graph_constant": 1021.1489,
                  "energy_total": 1243.1489, "seeds_satisfied": true,
                  "verified": true},
  "labels": [[0, 0, ...], ...]
}
```

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | liveness |
| POST | `/v1/validate` | dry-run validation report (no solve) |
| POST | `/v1/segment` | synchronous certified segmentation |
| POST | `/v1/jobs` | submit chunked background job (202) |
| GET  | `/v1/jobs/{id}` | state + chunk progress |
| POST | `/v1/jobs/{id}/cancel` | cooperative cancellation |
| GET  | `/v1/jobs/{id}/result` | certified result (409 until SUCCEEDED) |

Request body (either `unaries` or `image` + optional `data_model`):

```json
{
  "unaries": {"unary0": [[0.2, 3.0]], "unary1": [[2.0, 0.1]]},
  "pairwise": {"type": "potts", "weight": 1.0},
  "seeds": [{"row": 0, "col": 0, "label": 1}]
}
```

`pairwise` may also be `{"type": "table", "v00": .., "v01": .., "v10": ..,
"v11": ..}` for a general submodular potential. `image` accepts
`{"format": "array", "data": [[...]]}` or
`{"format": "png_base64", "data": "..."}`; with `data_model`
`{"type": "intensity_quadratic", "fg_mean": 200, "bg_mean": 60, "sigma": 25}`
data terms are `((I − mean)/sigma)²` (non-negative by construction).

## Error contract

All failures return `{"error": {category, code, message, details, run_id}}`
with distinguishable categories:

| category | HTTP | examples |
|----------|------|----------|
| `INPUT_VALIDATION` | 400 | `NON_SUBMODULAR_POTENTIAL`, `NEGATIVE_DATA_TERM`, `SEED_OUT_OF_BOUNDS`, `IMAGE_DECODE_FAILED` |
| `STATE_CONFLICT` | 409 | `SEED_CONFLICT`, `JOB_ALREADY_FINISHED`, `JOB_NOT_FINISHED` |
| `RESOURCE_EXHAUSTED` | 413/429 | `IMAGE_TOO_LARGE`, `CAPACITY_OVERFLOW`, `JOB_QUEUE_FULL` |
| `COMPUTATION_FAILURE` | 500 | `CERTIFICATE_MISMATCH`, `MAXFLOW_KERNEL_FAILURE` |
| `NOT_FOUND` | 404 | `JOB_NOT_FOUND` |

## Configuration (env vars)

`GRAPHCUT_MAX_PIXELS` (1e6), `GRAPHCUT_MAX_JOBS` (64),
`GRAPHCUT_CHUNK_ROWS` (64), `GRAPHCUT_ENERGY_TOLERANCE` (1e-6),
`GRAPHCUT_BIG_M_HEADROOM` (52).

## Testing

```bash
python3 -m pytest                 # 62 passed
python3 -m pytest tests/unit      # unit only
python3 -m pytest tests/integration
```

Real result at delivery: **62 passed** (Python 3.12, numpy 2.4, scipy
1.15, FastAPI 0.141).

What is verified and how:

- **Hand-computed answers** (`tests/integration/test_known_answers.py`):
  1×2 and 1×3 cases whose optima were derived by hand in comments —
  labels, energy, raw flow, graph constant and arc counts are asserted
  exactly. Reference answers are never produced by the core itself.
- **Brute-force enumeration** (`tests/integration/test_enumeration.py`):
  random submodular specs up to 3×3/2×4 with random hard seeds; all
  seed-consistent labelings are enumerated and scored by a plain-Python
  evaluator written *in the test file* (no `graphcut.energy` import), and
  the solver's energy, returned labeling and flow value must match the
  independent optimum.
- **Energy–graph correspondence** (`tests/unit/test_graph.py`): for every
  labeling of small random specs, the s-t cut capacity computed from the
  raw arc list equals the independently evaluated energy.
- **Kernel against literature** (`tests/unit/test_maxflow.py`): the CLRS
  classic flow network (max flow 23) plus degenerate graphs.
- **Failure classes**: non-submodular potentials, negative terms,
  conflicting seeds (409), oversize images (413), big-M overflow,
  job-queue limits (429), tampered certificates, illegal job transitions —
  each asserted with its exact error `category`/`code`.
- **Boundary & degenerate cases**: exact 4-neighbour edge lists for
  2×2/3×3/1×N images (no wrap-around), zero-smoothness reduces to
  per-pixel argmin, single-pixel images.

### Test logs

Each pytest run writes `test_logs/run-<timestamp>.log` containing the run
id, per-test rng seeds, solver-vs-brute-force energies and gaps, and the
reason for every expected rejection — enough to replay any failing case:

```
run_id=... event=certificate.verified reason='flow == cut == energy ...'
enum.trial shape=(3,3) trial=4 run_id=... solver=12.83... brute=12.83... gap=0.0 verdict=ok
rejected.capacity_overflow code=CAPACITY_OVERFLOW details={'big_m': 1e+300, ...}
```

Service runs log the same run-id-keyed intermediate states
(`spec.accepted`, `graph.built`, `kernel.solved`, `certificate.verified`).
