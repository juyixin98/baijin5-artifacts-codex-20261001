# Service call examples (curl)

Start the server:

```bash
uvicorn seamcarve.api:app --port 8000
```

Find one minimal seam (gradient energy):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/seam/find \
  -H 'Content-Type: application/json' \
  -d '{"pixels": [[10,20,30,40],[10,20,30,40],[50,60,70,80],[50,60,70,80]],
       "energy_mode": "gradient"}'
# -> seam.columns [0,0,0,0], energy 120.0 (hand-derived reference)
```

Find one minimal seam (forward energy):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/seam/find \
  -H 'Content-Type: application/json' \
  -d '{"pixels": [[10,20,30,40],[10,20,30,40],[50,60,70,80],[50,60,70,80]],
       "energy_mode": "forward"}'
# -> energy 30.0 (hand-derived reference)
```

Remove two seams as a chunked job (paths in ORIGINAL coordinates):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/carve \
  -H 'Content-Type: application/json' \
  -d '{"pixels": [[10,20,30,40],[10,20,30,40],[50,60,70,80],[50,60,70,80]],
       "n_seams": 2, "chunk_size": 1}'
# -> seams[1].points uses original column 1; chunks report progress 1,2
```

Failure: fully protected row -> HTTP 409 with category NO_LEGAL_SEAM:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/seam/find \
  -H 'Content-Type: application/json' \
  -d '{"pixels": [[10,20,30,40],[10,20,30,40],[50,60,70,80],[50,60,70,80]],
       "protect_mask": [[0,0,0,0],[0,0,0,0],[1,1,1,1],[0,0,0,0]]}'
# -> 409 {"error": {"category": "NO_LEGAL_SEAM", ...}}
```

Failure: malformed mask -> HTTP 422 with category CONTRACT_VIOLATION:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/seam/find \
  -H 'Content-Type: application/json' \
  -d '{"pixels": [[10,20,30,40],[10,20,30,40],[50,60,70,80],[50,60,70,80]],
       "protect_mask": [[0,0],[0,0]]}'
# -> 422 {"error": {"category": "CONTRACT_VIOLATION", ...}}
```

Recorded outputs from a real run are kept under `artifacts/smoke/`.
