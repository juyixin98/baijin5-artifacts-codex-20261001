# 字符串排序聚合比较契约 (collation-agg-contract)

A backend that groups / deduplicates strings under a **custom, versioned
collation**, implemented twice — a **sort-based** executor and a **hash-based**
executor — over typed **Arrow2** columnar batches, exposed through **Axum**.
A single verification entry point runs both strategies, compares them, checks
independently authored expectations, enforces rule-version isolation in
stateful sessions, and returns a categorized verdict (accepted / rejected /
undetermined) with diagnostics.

All data and participants are **local synthetic fixtures** — no production
accounts, no real business data, no network calls to external services.

---

## 1. What it does

Given rows of `(record_id, value)`:

1. Each original string `value` is turned into an **aggregation key**
   (`GroupKey`) by a versioned [`Collator`]. The original string is kept
   separately as the row's **identity**.
2. Rows are grouped two ways and the results must be identical:
   - **sort aggregation**: stable sort by `GroupKey`, then sweep equals;
   - **hash aggregation**: `HashMap<GroupKey, Group>`, output re-sorted by key.
3. Each equivalence class **retains a representative original value** (the
   first member in input order) — never the normalized key.
4. Verdicts are categorized and logged with the request/session id and the
   key state that explains the decision.

### Supported rule versions

| Version | Name | Semantics |
|--------:|------|-----------|
| `1` | `accent_case_num@1` | Unicode **NFKD**, strip combining marks, full **lowercase** folding, **natural-number** digit runs (`file2 < file10`). |
| `2` | `binary@2` | Raw Unicode scalar ordering; no case/accent/numeric folding. Used to prove version isolation. |

Unknown versions are **rejected**, never silently defaulted.

### The five contract guarantees (phases 2–3)

- **Equality semantics == sort key.** Grouping merges rows with the same
  `Eq` relation that `Ord` sorts by — one `GroupKey` type defines both.
- **Equivalence-class representative retained.** Output shows an original
  value (`"Café"`), not a normalized key (`"cafe"`).
- **Compatible hashing.** `GroupKey: Hash + Eq` over the same
  `(rule, null, fragments)` triple, so equal values collide in one bucket and
  the hash partition equals the sort partition (asserted on every request).
- **Rule versions never mix.** The rule version is *part of the key*, and a
  stateful session is pinned to its first batch's version; a switched batch
  is rejected (`reject_rule_version_mismatch`) and not folded in.
- **Identity ≠ aggregation key.** `record_id` travels independently; many
  distinct identities collapse onto one key and remain individually listed.

---

## 2. Module layout

Each module has a real responsibility (no single-file script, no empty
interface crate):

```
src/
  main.rs            # binary: `serve` (Axum) and `demo` (offline phase-3 cases)
  lib.rs             # crate wiring + shared re-exports
  error.rs           # AppError (operator faults only)
  config.rs          # Settings from env, independently constructable in tests
  collation/
    mod.rs           # Collator: value -> key (single keying authority)
    rules.rs         # Rule v1/v2, normalization, version registry
    key.rs           # GroupKey/Frag: Eq/Ord/Hash consistent, tokenizer
  batch.rs           # Arrow2 RecordBatch (record_id + nullable Utf8 value)
  operators/
    keyed.rs         # key typed Arrow columns into RowKey
    sort.rs          # sort-based grouping executor
    hash.rs          # hash-based grouping executor
    dedup.rs         # first-in-order dedup retaining original values
    group.rs         # Group / ExecOutput / KeyWarnings
  state.rs           # AppState, version-pinned Sessions, DiagLog, redaction
  validate.rs        # THE contract: run both, compare, expect, session, verdict
  api.rs             # thin Axum transport over validate
tests/
  common/mod.rs      # synthetic fixtures + an INDEPENDENT oracle (own impl)
  contract.rs        # phase 2/3 behavioral + property tests
  api.rs             # end-to-end Axum router tests
```

---

## 3. Local run

Requires a recent stable Rust (built/verified on 1.98). Dependencies are
locked in `Cargo.lock`.

```bash
# build (uses Cargo.lock exactly)
cargo build --locked

# run every test (unit + integration)
cargo test --locked

# offline acceptance run of the phase-3 cases (no server / no network)
cargo run -- demo

# start the HTTP API
cargo run -- serve
# -> listening on 127.0.0.1:8080 by default
```

Settings (env, all optional): `BIND_ADDR`, `MAX_ROWS`, `MAX_VALUE_BYTES`,
`REDACT_SENSITIVE`, `DIAG_HISTORY`.

> If the global Cargo cache is contended on a shared machine, point at a
> project-local home: `CARGO_HOME=$PWD/.cargo-home cargo test`.

---

## 4. Example requests

Health:

```bash
curl -s localhost:8080/health
```

Verify accent/case + composed/decomposed equivalence, and independently
assert there is exactly 1 group:

```bash
curl -s -X POST localhost:8080/verify \
  -H 'content-type: application/json' \
  -d @examples/accent.json
```

Numeric natural ordering (`file2` before `file10`):

```bash
curl -s -X POST localhost:8080/verify \
  -H 'content-type: application/json' \
  -d @examples/numeric.json
```

Rule-switch rejection — send the two session files in order; the **second is
rejected** because the session is pinned to v1:

```bash
curl -s -X POST localhost:8080/verify -H 'content-type: application/json' \
  -d @examples/session_v1.json
curl -s -X POST localhost:8080/verify -H 'content-type: application/json' \
  -d @examples/session_v2_switch.json   # category=reject_rule_version_mismatch
```

Inspect recent diagnostics (request id, reason code, counts — no raw values):

```bash
curl -s localhost:8080/diagnostics
```

### Verdict categories

| `category` | Meaning |
|---|---|
| `accepted` | Both executors agree and any independent expectation matched. |
| `reject_unknown_rule` | Rule version not registered. |
| `reject_empty_record_id` / `reject_duplicate_record_id` | Identity validation. |
| `reject_too_many_rows` / `reject_value_too_large` | Configured limits. |
| `reject_rule_version_mismatch` | A stateful session received a different version. |
| `reject_expected_count` / `reject_expected_distinct` | Independent answer disagreed. |
| `undetermined_numeric_overflow` | A digit run exceeded `u64`; values may have collapsed. |
| `undetermined_executor_mismatch` | Sort and hash partitions differed (should never happen). |

Rejections are returned with **HTTP 200** and a categorized body so clients
branch on `category`; only true operator faults return 5xx.

---

## 5. Diagnostics & sensitive data

Every decision appends a bounded record to `/diagnostics` containing the
request id, optional session id, rule version, a stable `reason_code`, the
row/group counts, and a human `detail` that explains *why* it was accepted,
rejected, or is undetermined. **Diagnostic text never embeds raw values.**
With `"sensitive": true`, group representatives in the *response* are
redacted to `<str bytes=N fp=...>` (length + non-reversible fingerprint), and
the same redaction helper backs any value-bearing log path.

---

## 6. Independent reference answers

Tests do **not** derive expected results from the code under test:

- expected group counts / distinct representatives are **hand-authored
  literals**;
- `tests/common/mod.rs` contains a **second, independently written** v1
  oracle (explicit accent table + `u128` numerics, no NFKD code path), and a
  property test over 50 seeded pseudo-random inputs checks both that sort and
  hash agree and that the count equals the oracle;
- failure paths assert the **specific category and message**, not merely that
  an endpoint is callable.

---

## 7. Scope & key trade-offs

- **Local, deterministic, in-memory.** No database or persistence; sessions
  and diagnostics live for the process lifetime.
- **Two deliberately small rules.** v1 covers exactly the exercised
  behaviors (accent, case, composition, natural digits). Accent folding
  removes combining marks in U+0300–U+036F after NFKD; locale-specific
  collation (German ß, Turkish dotless ı, multi-level tie-breaking) is out of
  scope. v2 is a raw binary control.
- **Numeric tokens are `u64`.** A wider run saturates and makes the verdict
  `undetermined` rather than silently merging distinct values. The oracle
  deliberately uses `u128` so it can see what the core cannot.
- **NULL policy:** SQL NULLs form exactly one group, sorted after real
  values, with an empty rendered representative.
- **Arrow2** is the real typed-batch representation (`Utf8Array<i32>` with
  validity), not a `Vec` facade; compute happens by reading columnar arrays.
