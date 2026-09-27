# Variant Resource Service

A resource service that serves one resource in **multiple media
representations and languages**, performing explainable HTTP content
negotiation on `Accept` and `Accept-Language`.

It is built with **TypeScript + Node.js + Fastify + SQLite** (SQLite uses the
built-in `node:sqlite` module — no native build step). All data is local,
synthetic seed data; there are no external accounts or services.

The negotiation rules are specified in
[`docs/NEGOTIATION.md`](./docs/NEGOTIATION.md).

## Why this exists

Demonstrates a content-negotiation implementation where:

* **Zero weight is an explicit prohibition**, never "unspecified".
* **Wildcard priority is correct**: a concrete range outranks a wildcard even
  when the wildcard appears later, and same-weight ties are **stable**.
* Invalid weights, duplicate items and unknown parameters follow one
  documented policy each.
* Media type and language are negotiated **independently**, with a distinct
  failure when they cannot be satisfied together.
* `Vary` matches exactly the headers that actually influenced the choice.
* Every decision is **explainable**: per-candidate scores, ordered steps and
  a classified failure, all correlated by a run id.

## Requirements

* Node.js **>= 22.5** (uses the built-in `node:sqlite`; tested on v22.23).
* npm. The only runtime dependency is `fastify`; dev dependencies are
  `typescript` and `@types/node`. All versions are pinned exactly.

## Install, build, run

```bash
npm install        # pinned deps
npm run build      # tsc -> dist/ + copy SQL/JSON fixtures
npm start          # serves on 127.0.0.1:8080 (config/config.json)
```

On first start the SQLite database (`data/app.db`) is created and seeded from
`data/seed.json`. Reseed explicitly with:

```bash
npm run seed            # idempotent (replaces seed rows)
npm run seed -- --reset # clear then seed
```

### Configuration

`config/default.json` is the standalone config file. It can be overridden by
a file pointed to with `RESOURCE_SERVICE_CONFIG`, plus a small environment
allow-list: `HOST`, `PORT`, `DB_FILE`.

| key | values | meaning |
|-----|--------|---------|
| `negotiation.absentAccept` | `wildcard` (default) / `default` | absent `Accept` means `*/*` or the configured default media type |
| `negotiation.absentAcceptLanguage` | `wildcard` / `default` | same for `Accept-Language` |
| `negotiation.unknownParameters` | `ignore` (default) / `reject` | ignore `accept-ext` after `q` with a notice, or reject with 400 |
| `negotiation.languageFallback` | `lookup` (default) / `filtering` | allow `en-us` → `en` truncation, or strict RFC basic filtering |

Run on an alternate port/database (useful when 8080 is taken):

```bash
PORT=8091 DB_FILE=data/verify.db npm start
```

## HTTP API

| method & path | purpose |
|---------------|---------|
| `GET /healthz` | liveness |
| `GET /resources` | catalogue: resources and the variants each offers |
| `GET /resources/:id` | the resource, content-negotiated |
| `GET /resources/:id/trace` | **diagnostic**: full explanation of the decision, no body |

Successful resource responses set `Content-Type`, `Content-Language`,
`Vary`, `X-Selected-Representation`, and `X-Negotiation-Run-Id`.

### Example calls

```bash
# Exact JSON in English
curl -s -H 'Accept: application/json' -H 'Accept-Language: en' \
  http://127.0.0.1:8080/resources/article-001

# Prefer the versioned vendor type in French
curl -s -H 'Accept: application/vnd.shop.v2+json' -H 'Accept-Language: fr' \
  http://127.0.0.1:8080/resources/article-001

# Weighted media preference (XML wins)
curl -s -H 'Accept: application/json;q=0.4, application/xml;q=0.9' \
  -H 'Accept-Language: en' http://127.0.0.1:8080/resources/article-001

# HTML explicitly prohibited, wildcard admits everything else
curl -s -i -H 'Accept: text/html;q=0, */*' -H 'Accept-Language: en' \
  http://127.0.0.1:8080/resources/article-001

# Truncation language fallback (en-US served en)
curl -s -H 'Accept: application/json' -H 'Accept-Language: en-us' \
  http://127.0.0.1:8080/resources/article-001

# Explain the decision (per-candidate scores + ordered steps)
curl -s -H 'Accept: application/xml' -H 'Accept-Language: fr' \
  http://127.0.0.1:8080/resources/article-001/trace
```

Failure responses are explicit and never 200:

* `400` — `MALFORMED_HEADER`, `INVALID_WEIGHT`, `DUPLICATE_PARAMETER`,
  `UNKNOWN_PARAMETER`.
* `406` — `UNACCEPTABLE_MEDIA_TYPE`, `UNACCEPTABLE_LANGUAGE`,
  `NO_VARIANT_FOR_COMBINATION`.
* `404` — unknown resource (distinct from negotiation failure).

```bash
curl -s -i -H 'Accept: */*;q=0' http://127.0.0.1:8080/resources/article-001
# HTTP/1.1 406 ...  {"error":"UNACCEPTABLE_MEDIA_TYPE", ...}

curl -s -i -H 'Accept: text/html;q=1.5' http://127.0.0.1:8080/resources/article-001
# HTTP/1.1 400 ...  {"error":"INVALID_WEIGHT", ...}
```

## Architecture

Real, separately tested modules — no single-file script, stubs or hard-coded
output:

```
src/
  contract/              # RFC parsing layer (pure string handling)
    tokenizer.ts         #   shared token / quoted-string / element scanner
    weight.ts            #   strict qvalue parser
    accept-parser.ts     #   Accept: ranges, constraints, q, extensions, dupes
    language-parser.ts   #   Accept-Language: tags, wildcard, q, dupes
    errors.ts            #   unified error taxonomy
  core/                  # execution kernel (pure, deterministic)
    negotiator.ts        #   scoring, prohibition, fallback, ties, trace
    types.ts             #   domain model + NegotiationTrace
    version.ts           #   service version for traces/logs
  state/                 # state adapter
    repository.ts        #   SQLite lifecycle, seeding, row -> domain mapping
    schema.sql           #   relational schema
  http/                  # Fastify adapter
    app.ts               #   routes: catalogue, resource, trace
    present.ts           #   content-type + error -> HTTP status mapping
    negotiation-log.ts   #   structured per-run JSON logging
  config.ts              # config load + validation (fails fast)
config/default.json      # standalone configuration
data/seed.json           # synthetic local fixtures
scripts/seed.ts          # standalone seeder
test/
  contract/ core/ state/ http/   # node:test suites (assert concrete results)
  fixtures/grid.json             # hand-authored negotiation grid
  oracle/                        # independent reference implementation + fuzz
```

The dependency direction is one-way: `core` does not import `state` or
`http`, so the negotiation kernel is a pure function trivially testable
against in-memory fixtures.

## Tests

```bash
npm test          # build, then run all node:test suites from dist/
npm run test:oracle   # independent oracle: hand vectors + differential fuzz
```

Tests assert **specific winners and failure categories**, not "endpoint
reachable". Coverage includes: wildcard priority and ordering, every
candidate prohibited, absent headers, parameter matching, language
prefix/truncation fallback, the media/language combination failure, `Vary`
dimensions, deterministic ties, parse-error classification, and SQLite
integration.

### Independent oracle

`test/oracle/oracle.mjs` is a **second, independently written**
implementation (regex/split based, flat numeric scores) that imports no
production code. `run-oracle.mjs`:

1. Checks 15 **hand-authored vectors** whose expected answers were written by
   hand, not generated by either implementation.
2. Runs **seeded differential fuzzing** (default 500 cases; pass
   `iterations seed`, e.g. `npm run test:oracle -- 2000 777`) comparing the
   oracle against the production core.

Both must agree on winner id or failure code. Every log line carries the run
id, service version and progress; any parse error, thrown exception or
disagreement exits non-zero.

## Logging

Each negotiation emits one structured JSON line with `runId`,
`serviceVersion`, raw `accept`/`acceptLanguage`, parsed ranges, per-variant
scores, ordered `steps`, `vary`, and the winner or failure code. Failures log
at `warn`, successes at `info`. Use the response header
`X-Negotiation-Run-Id` to correlate a specific request with its log line and
the `/trace` body.

## Known limitations

* `node:sqlite` is an experimental Node 22 API; a `--disable-warning` flag
  suppresses its startup warning. Behavior is stable here but the API could
  change in future Node majors.
* Only `Accept` and `Accept-Language` are negotiated; `Accept-Charset`
  (bodies are UTF-8) and content-encoding/gzip are out of scope.
* Language tag parsing validates structural shape (subtag length/charset),
  not the full BCP 47 registry; unknown primary tags are still structurally
  accepted.
* Media `accept-ext` parameters after `q` are not interpreted (ignored by
  default, or rejected by configuration), matching the fact that the server
  defines no extensions.
* There is no authentication, rate limiting or persistence clustering —
  appropriate for a local demonstration service, not direct internet
  exposure.
