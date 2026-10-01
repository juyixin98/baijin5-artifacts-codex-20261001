# Service call examples

All examples assume the server is running locally:

```bash
cargo run --bin lftj-server
# listening on 127.0.0.1:8080
```

No authentication or external accounts are involved; all data is supplied
inline or registered from local synthetic fixtures.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + effective limits |
| GET | `/relations` | registered catalog relation names |
| POST | `/admin/relations` | register local fixture relations |
| POST | `/query` | JSON join request/response |
| POST | `/query/arrow` | same request; response is an Arrow2 IPC stream |

## 1. Health

```bash
curl -s http://127.0.0.1:8080/health
```

```json
{
  "default_limit": 1024,
  "max_access_budget": 100000000,
  "max_limit": 100000,
  "max_relations": 6,
  "service": "leapfrog-triejoin",
  "status": "ok"
}
```

## 2. Triangle query (three-table natural join)

`R(a,b) ⋈ S(b,c) ⋈ T(a,c)`:

```bash
curl -s http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  -d @fixtures/requests/triangle.json
```

Response highlights:

```json
{
  "request_id": "req-00000001-9f3c2a1b",
  "decision": "accepted",
  "columns": [{"name":"a","type":"int"},{"name":"b","type":"int"},{"name":"c","type":"int"}],
  "row_count": 10,
  "rows": [[0,1,2], "..."],
  "stats": {
    "emitted_rows": 10,
    "intermediate_tuples_materialized": 0,
    "stop_reason": "complete"
  },
  "naive": { "matches": true, "naive_intermediate_tuples_materialized": 30 },
  "next_cursor": null
}
```

The same request as Arrow2 IPC (headers carry decision/counters; body is
`application/vnd.apache.arrow.stream`):

```bash
curl -s -D - http://127.0.0.1:8080/query/arrow \
  -H 'content-type: application/json' \
  -d @fixtures/requests/triangle.json -o /tmp/triangle.arrows
```

## 3. Pagination / resumable interruption

```bash
curl -s http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  -d @fixtures/requests/page1.json
# -> "stop_reason":"output_limit", "next_cursor":"<opaque>"
```

Replay with the returned cursor (single use, bound to the exact query):

```bash
curl -s http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  -d '{"...same relations...","limit":5,"cursor":"<opaque>"}'
```

Continue until `next_cursor` is `null` and `stop_reason` is `complete`. The
union of pages is exactly the one-shot result — no lost or duplicated rows.

## 4. Access-budget interruption -> undecidable, then resume

```bash
curl -s -i http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  -d @fixtures/requests/budget.json
# HTTP/1.1 202 Accepted
# "decision":"undecidable", "stats":{"stop_reason":"budget_exhausted"},
# "next_cursor":"<opaque>"
```

Resume without the budget to get the exact complement and an `accepted`
`complete` response.

## 5. Failure categories (stable codes)

| Situation | HTTP | `error.code` |
|---|---|---|
| Body is not JSON | 400 | `malformed_json` |
| Valid JSON, bad schema / bad cell type / arity | 400 | `invalid_request` |
| Catalog name not found | 422 | `unknown_relation` |
| Shared column has different types | 422 | `type_mismatch` |
| No shared columns (would be a Cartesian product) | 400 | `disjoint_schema` |
| NULL in a join key under default policy | 400 | `null_key` |
| Disconnected relation or > `max_relations` | 422 | `unsupported_shape` |
| Unknown/expired/foreign cursor | 400 | `invalid_cursor` |

Error body shape:

```json
{
  "request_id": "req-00000007-2b7d91aa",
  "decision": "rejected",
  "error": { "code": "null_key", "message": "..." }
}
```

The `x-request-id` header on every response correlates with the server's
structured log line for the same decision. Logs and error messages never
contain cell values — a value is rendered as `<int>`, `<bool>` or
`<string:len=N>`.

## 6. NULL policy

- default/`"reject"` — a NULL join key is rejected loudly;
- `"null_policy":"sql_match_never"` — NULL-key rows are excluded (NULL matches
  nothing, not even NULL), e.g.:

```json
{ "relations": [ "..." ], "null_policy": "sql_match_never" }
```
