# Content Negotiation Specification

This document is the authoritative, human-readable contract implemented by
`src/contract` (parsers) and `src/core/negotiator.ts` (selection kernel).
Every rule below is asserted by tests and cross-checked by the independent
oracle in `test/oracle/`.

References: RFC 9110 §12.4.2 (quality values), §12.5.1 (`Accept`),
§12.5.3 (`Accept-Language`), and RFC 4647 §3.4 (lookup) for truncation.

## 1. Quality values

A quality value is exactly: `0`, `1`, or `0`/`1` followed by a decimal point
and 1–3 digits (`0.5`, `0.123`, `1.00`). Rejected as `INVALID_WEIGHT`
(HTTP 400): `1.5`, `2`, `-0.1`, `0.1234`, `1.0000`, `1.`, `+1`, `0x1`,
non-numeric text, and quoted values (`q="0.5"`).

`q=0` is a **legal, meaningful value**. It means "explicitly prohibited",
never "unspecified". Absence of a quality means `q=1`.

## 2. Accept (media type)

### 2.1 Ranges and specificity

Three shapes, from most to least specific:

| shape | example | specificity |
|-------|---------|-------------|
| exact type/subtype | `application/json` | 2 |
| type wildcard | `application/*` | 1 |
| full wildcard | `*/*` | 0 |

A variant media type matches a range when its type and subtype match
(wildcards standing for any value), **and** every parameter declared on the
range before `q` is present on the variant with the same (case-insensitive)
value.

### 2.2 Parameters: before vs after `q`

* Parameters **before** `q` are *match constraints*. A variant missing the
  parameter, or with a different value, does **not** match that range.
  Example: `application/json; version=2; q=0.9` only matches JSON whose
  media parameters include `version=2`.
* Tokens **after** `q` are `accept-ext` extensions. The server implements no
  extensions, so by default they are ignored and an
  `IGNORED_EXTENSION_PARAMETER` notice is recorded. With
  `negotiation.unknownParameters = "reject"` they produce `UNKNOWN_PARAMETER`
  (HTTP 400).

### 2.3 Which range supplies the quality

For a given variant, the **most specific matching range** supplies the media
quality. A concrete `text/html;q=0` therefore forbids HTML even when a later
`*/*;q=1` exists. When two matching ranges have equal specificity, the first
one in the header wins (header order is the stable tie-break).

## 3. Accept-Language

### 3.1 Tags and specificity

Language tags are case-insensitive BCP 47-ish sequences: a 1–8 letter primary
subtag followed by zero or more `-`-separated 1–8 character alphanumeric
subtags. `*` is the wildcard. Malformed tags (`en-`, `-en`,
`en-TOOLONGSUB`, …) produce `MALFORMED_HEADER` (HTTP 400). Only a `q`
parameter is allowed on a language range.

### 3.2 Matching tiers

A served tag is matched to a requested range at one of these tiers:

| tier | relation | example (range → served) |
|------|----------|--------------------------|
| 3 exact | equal tags | `en-gb` → `en-gb` |
| 2 prefix (filtering) | served starts with range | `en` → `en-gb` |
| 1 truncation fallback (lookup) | range starts with served | `en-us` → `en` |
| 0 wildcard | `*` matches anything | `*` → `fr` |

* The highest matching tier supplies the language quality.
* Tier 1 (truncation) is enabled by `languageFallback: "lookup"` (default).
  Set it to `"filtering"` for strict RFC 9110 basic filtering, where
  `en-us` will **not** fall back to `en`.
* `q=0` at tier 2 also bans the more specific served tag: `en;q=0` forbids
  both `en` and `en-gb`.

## 4. Combining the two axes

Media type and language are scored independently for **every** variant:

```
combined = mediaQuality * languageQuality
```

* If either axis has no matching range for a variant, it is not selectable.
* If either matching quality is `0`, the variant is explicitly prohibited
  (`combined = 0`, recorded as such in the trace).

### 4.1 Failure classification (deterministic, ordered)

Checked in this order; each maps to HTTP 406:

1. No variant matches **any** media range → `UNACCEPTABLE_MEDIA_TYPE`.
2. Every media match carries `q=0` → `UNACCEPTABLE_MEDIA_TYPE`.
3. No variant matches **any** language range → `UNACCEPTABLE_LANGUAGE`.
4. Every language match carries `q=0` → `UNACCEPTABLE_LANGUAGE`.
5. Both axes individually allow something, but no **single** variant
   satisfies both → `NO_VARIANT_FOR_COMBINATION`.

Parse errors (cases 1–4 of sections 2–3) short-circuit earlier and return
HTTP 400 with one of `MALFORMED_HEADER`, `INVALID_WEIGHT`,
`DUPLICATE_PARAMETER`, `UNKNOWN_PARAMETER`.

### 4.2 Winner tie-break (stable same-weight ordering)

Among selectable variants, the winner is chosen by, in order:

1. `combined` quality, higher first;
2. media specificity, higher first;
3. language tier, higher first;
4. server declaration order (`ordinal`), lower first;
5. representation id, lexicographic.

Steps 4–5 guarantee a deterministic result across runs and processes even
when every quality is identical.

## 5. Absent headers

* An absent/blank `Accept` is treated as `*/*` by default
  (`absentAccept: "wildcard"`). With `"default"` it expands to the configured
  `defaultMediaType`.
* The same applies to `Accept-Language` (`*` / `defaultLanguage`).

## 6. Duplicate and unknown-input policy

| input | policy | result |
|-------|--------|--------|
| same parameter twice in one element | always reject | 400 `DUPLICATE_PARAMETER` |
| `q` declared twice | always reject | 400 `DUPLICATE_PARAMETER` |
| identical range repeated across elements | keep first, ignore rest | `DUPLICATE_RANGE` notice |
| extension after `q` | ignore (default) / reject | notice / 400 `UNKNOWN_PARAMETER` |
| structurally malformed element | always reject | 400 `MALFORMED_HEADER` |
| illegal weight | always reject | 400 `INVALID_WEIGHT` |

## 7. `Vary`

`Vary` lists exactly the request headers that could have changed the chosen
variant for this resource:

* A header that was actually **sent** is always listed (it is a real cache
  discriminator even if the resource happens not to vary on it).
* A header that was **absent** (replaced by a synthetic wildcard) is listed
  only when the resource offers variation on that axis — multiple distinct
  media types/parameters for `Accept`, multiple languages for
  `Accept-Language`.
* Parse failures vary on the single malformed header that determined the 400.

## 8. Explainability

Every negotiation returns a trace (`GET /resources/:id/trace`) containing a
`runId`, `serviceVersion`, raw headers, parsed ranges, one score line per
variant, an ordered `steps` log (parse → score → verdict), the `vary` list,
and either a `winner` or a classified `failure`. The same record is emitted
as one structured JSON log line, so a `runId` ties a request, its decision
basis, and its outcome together. A failure is never logged or returned as
success.
