# Algorithm: multi-relation Leapfrog Triejoin

This is a generalized version of the Leapfrog Triejoin algorithm
(Veldhuizen, *Leapfrog Triejoin: a worst-case-optimal join algorithm*). The
three-table triangle query is simply the arity-3 case; there is no
special-casing for exactly three relations.

## 1. Ordered tries with explicit multiplicity

Each relation is stored as a prefix trie (`src/trie/mod.rs`):

1. Rows are projected into a **trie column order**: the join variables the
   relation participates in (in one global join-variable order) come first,
   followed by its private attributes.
2. Rows are sorted and run-length grouped: identical tuples collapse to one
   trie leaf carrying a **bag multiplicity**. Duplicate rows therefore survive
   as counts rather than being silently deduplicated (set vs bag semantics).
3. Every node also stores the **sum of multiplicities in its subtree**, so a
   relation's multiplicity for a partial assignment is readable at any prefix
   depth (needed when private columns are projected away).

The global join-variable order is attribute first-appearance order restricted
to variables bound by ≥ 2 relations. Every relation trie's leading levels line
up with this order, which is what lets cursors at the same depth intersect.

## 2. Leapfrog cursor

A cursor (`Cursor`) over a trie supports `open_root`/`open`/`up` across levels,
`next`, `at`, and the crucial operation:

* `seek(target)` — move to the first key `≥ target` at the current level via
  **binary search** over the contiguous, sorted sibling range. It jumps over
  every key below `target`; it never walks them.

Each evaluation owns a `Counters` record: seek *calls*, seek *key comparisons*
(log N per seek), `next` calls, open/up, value probes, and emitted
assignment/multiplicity totals. Tests use these to prove intersection by
leaping rather than scanning.

## 3. Solving one variable

For global variable `d`, every relation that binds it (`plan.owners[d]`) opens a
cursor at that variable's trie level. The leapfrog intersection is the
canonical repeated step:

1. Find the cursor on the **minimum** current key and the one on the
   **maximum** current key.
2. If min == max, every active cursor agrees: that key is in the intersection.
   Bind it, recurse to the next variable, then advance every cursor once.
3. Otherwise `seek` the minimum cursor **to the maximum key**. Binary search
   skips the whole gap; if it overshoots, that key becomes the new maximum and
   the roles rotate in the next round.
4. Any cursor reaching the end ends that variable's enumeration.

Because each step raises the global minimum until all cursors meet, no
intermediate pair-relation result is built. Work at each variable is bounded by
the standard LFJ leapfrog bound over the active domains, which gives the
worst-case-optimal behaviour; ordering the global variables by first appearance
on a connected join graph keeps each recursion intersecting related relations.

## 4. Restricted multi-table natural join

"Restricted" is enforced during validation (`src/query/mod.rs`), before the
engine runs:

* The relation/attribute graph must be **connected** (union–find). A
  disconnected relation would multiply as a Cartesian product and is rejected
  with `disconnected_join_graph`.
* Multiple relations must share at least one attribute (`no_common_attribute`).
* Shared attributes must share a type (`type_mismatch`).
* Relation count is capped (`MAX_RELATIONS`); rows-per-relation and output
  limits are enforced as resource limits.

These guarantees are why it is safe to intersect rather than ever enumerate a
standalone product.

## 5. NULL strategy

SQL natural-join equality treats NULL as never equal to NULL. The policy is
explicit, not implicit:

* `reject` (default): any NULL on a join column fails validation with
  `null_in_join_key`. No row is silently lost.
* `drop_join_rows`: such rows are excluded from the join, and the number
  dropped per relation is reported in diagnostics (`null_join_rows_dropped`).

NULL in a **private** (non-join) column is preserved through to output; the
Arrow columns are nullable and a round-trip test covers this.

## 6. Private attributes and bag multiplicity

After all join variables are bound, each aligned relation cursor may still have
private levels beneath it. The engine enumerates each relation's private
completions (`Trie::collect_leaves`) and combines them in canonical
relation-declaration order. The output tuple's multiplicity is the **product**
of the aligned leaf multiplicities (e.g. 3 duplicates on the left × 2 on the
right = 6). This per-assignment enumeration produces the answer itself; it is
bounded by the answer size and is not a stored join intermediate.

## 7. Output cap and resumable cursors

Canonical output is the join variables followed by private attributes in
relation order, so the full result has a deterministic lexicographic order.

* **No projection:** traversal emits in canonical order and stops after
  `limit` rows. A resumption token stores the last emitted tuple; on resume,
  while the bound prefix matches, cursors `seek` directly to the lower bound
  (`after[var]`) — the page boundary is reached by leaping, not by re-scanning.
* **Projection:** dropping/reordering columns can make equal projected keys
  non-adjacent in canonical order, so projected bag multiplicities are
  aggregated over the complete traversal into a sorted multiset, then the page
  is sliced after the decoded lower bound. An aggregated group is never split
  across pages. The join itself still builds no intermediate product; only the
  necessary distinct projected output is held.

Tokens (`src/join/cursor.rs`) are versioned, URL-safe base64 of a JSON tuple,
and validated for arity and per-column type; a corrupt or mismatched token is
rejected with `invalid_cursor`.

## 8. Independent reference oracle

`src/join/naive.rs` is a deliberately naive left-deep full enumeration built on
hash maps, sharing **no** code with the engine. It materializes the running
product after each binary join (recording its size) and independently applies
NULL policy, projection, and multiplicity aggregation. Tests treat it as the
oracle for exact multiset agreement and as the instrument that demonstrates the
intermediate blowup LFJ avoids.
