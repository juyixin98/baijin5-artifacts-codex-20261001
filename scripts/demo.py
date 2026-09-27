#!/usr/bin/env python3
"""Local end-to-end demonstration (no network, no external accounts).

Runs the real pipeline against a throwaway SQLite database under ./data:

  1. ingest a synthetic corpus containing intra-transaction duplicates,
     duplicate transactions and empty transactions;
  2. start a mining job with a deliberately tiny enumeration budget, print
     the partial result, then advance it slice by slice until completion;
  3. show closed vs maximal itemsets as distinct concepts;
  4. independently verify support and closure through the SQL index;
  5. trigger one of each failure category and print its stable error code.

Run:
    python scripts/demo.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cfim.config import Settings, load_settings  # noqa: E402
from cfim.corpus import normalize_corpus, support_threshold  # noqa: E402
from cfim.errors import DomainError  # noqa: E402
from cfim.kernel import advance, initial_state  # noqa: E402
from cfim.store import Store  # noqa: E402

SYNTHETIC_TRANSACTIONS = [
    ["a", "b", "c", "a"],   # intra-transaction duplicate -> {a,b,c}
    ["a", "b", "c"],        # duplicate transaction keeps its own tid
    ["a", "b", "c"],        # and a third identical row
    ["c", "d"],
    ["d"],
    [],                     # empty transaction, retained
]
MIN_SUPPORT = 2
SLICE_BUDGET = 3


def _hr(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    request_id = f"demo-{uuid.uuid4().hex[:12]}"
    print(f"request_id: {request_id}")

    settings = Settings(
        db_path=ROOT / "data" / "demo.db",
        log_level=load_settings().log_level,
        default_budget=SLICE_BUDGET,
        max_transactions=10_000,
        max_items_per_transaction=256,
        max_item_length=128,
        max_corpus_name_length=200,
        max_advance_budget=100_000,
    )
    if settings.db_path.exists():
        settings.db_path.unlink()
    store = Store(settings.db_path)

    # 1. Normalization ------------------------------------------------------
    _hr("step 1: corpus normalization (cfim.corpus)")
    normalized = normalize_corpus("demo-corpus", SYNTHETIC_TRANSACTIONS, settings)
    print(f"raw transactions       : {len(SYNTHETIC_TRANSACTIONS)}")
    print(f"stored transactions    : {len(normalized.transactions)}")
    print(f"empty transactions     : {normalized.empty_transaction_count}")
    print(f"duplicate item hits    : {normalized.duplicate_item_occurrences}")
    print(f"item domain            : {list(normalized.item_domain)}")
    for tid, tx in enumerate(normalized.transactions, start=1):
        print(f"  tid={tid}: {list(tx)}")
    record = store.create_corpus(normalized)
    print(f"corpus_id              : {record.corpus_id}")

    # 2. Budgeted, resumable mining ----------------------------------------
    _hr(f"step 2: mining kernel, min_support={MIN_SUPPORT}, "
        f"budget={SLICE_BUDGET} per slice")
    db = store.load_vertical_database(record.corpus_id)
    support_threshold(MIN_SUPPORT, db.transaction_count)
    state = initial_state(db, MIN_SUPPORT)
    job_id = store.create_job(record.corpus_id, MIN_SUPPORT, state)
    print(f"job_id                 : {job_id}")

    slice_no = 0
    while True:
        slice_no += 1
        advance(state, SLICE_BUDGET)
        store.save_job_state(job_id, state)
        print(
            f"  slice {slice_no}: nodes_visited={state.nodes_visited} "
            f"status={'PARTIAL' if not state.completed else 'COMPLETED'} "
            f"closed_so_far={len(state.results)}"
        )
        for r in state.results:
            print(f"      {list(r.itemset)} support={r.support}")
        if state.completed:
            break

    # 3. Closed vs maximal --------------------------------------------------
    _hr("step 3: closed vs maximal (distinct concepts)")
    for r in state.results:
        kind = "MAXIMAL (also closed)" if r.maximal else "closed (has a frequent strict superset)"
        print(f"  {list(r.itemset)} support={r.support} -> {kind}")

    # 4. Independent verification ------------------------------------------
    _hr("step 4: independent verification through the SQL index")
    for probe in (("a",), ("a", "b"), ("d",), ("c", "d")):
        tids = store.tidset_for_itemset(record.corpus_id, probe)
        closure = store.closure_of_itemset(record.corpus_id, probe)
        is_closed = tuple(sorted(probe)) == tuple(sorted(closure))
        print(
            f"  {list(probe)}: support={len(tids)} tids={list(tids)} "
            f"closure={list(closure)} closed={is_closed}"
        )

    # 5. Failure categories -------------------------------------------------
    _hr("step 5: failure semantics (stable error codes)")
    for label, invoke in (
        ("threshold above transaction count", lambda: support_threshold(99, db.transaction_count)),
        ("unknown item in query", lambda: store.tidset_for_itemset(record.corpus_id, ("zzz",))),
        ("missing corpus", lambda: store.get_corpus("does-not-exist")),
    ):
        try:
            invoke()
        except DomainError as exc:
            print(f"  {label:35s} -> code={exc.code.value:24s} reason={exc.message}")

    store.close()
    _hr(f"done (request_id={request_id}, db={settings.db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
