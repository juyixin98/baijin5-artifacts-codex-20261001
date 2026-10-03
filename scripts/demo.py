#!/usr/bin/env python3
"""Local end-to-end demonstration (no network accounts, no real data).

It indexes the bundled synthetic reference and queries every bundled read,
printing concrete candidate locations and the verdict basis. It also walks
through each documented error category to show that failures are explicit,
never folded into a success response.

Run:
    python scripts/demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from miniseed.config import Settings  # noqa: E402
from miniseed.errors import MiniseedError  # noqa: E402
from miniseed.logging_setup import configure_logger, log_event, log_versions  # noqa: E402
from miniseed.sequence import parse_fasta_records, parse_sequence  # noqa: E402
from miniseed.service import MiniseedService, new_request_id  # noqa: E402
from miniseed.store import SeedStore  # noqa: E402

K, W = 9, 5
RUN_ID = "demo-syn-001"


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    logger = configure_logger(ROOT / "logs" / "demo.log")
    run_identity = new_request_id()
    log_versions(logger, run_identity)

    db_path = ROOT / "data" / "demo.db"
    if db_path.exists():
        db_path.unlink()
    settings = Settings(k=K, w=W, db_path=db_path)

    with SeedStore(db_path) as store:
        svc = MiniseedService(store, settings)

        ref_doc = (ROOT / "fixtures" / "reference_syn.txt").read_text()
        _, ref_seq = parse_fasta_records(ref_doc)[0]
        ref = parse_sequence(ref_seq, name="reference").sequence

        _hr(f"1) INDEX reference  run={RUN_ID}  k={K} w={W}")
        idx = svc.index_reference(RUN_ID, ref, ref_name="syn_ref_v1",
                                  k=K, w=W, overwrite=True)
        store.audit("demo_index", run_identity,
                    {"seeds": idx.seed_count, "length": idx.ref_length},
                    run_id=RUN_ID)
        print(f"reference length      : {idx.ref_length}")
        print(f"windows scanned       : {idx.windows} "
              f"(N-only: {idx.windows_without_kmer})")
        print(f"seed records          : {idx.seed_count}")
        print(f"distinct seed values  : {idx.distinct_seed_values}")
        print(f"largest bucket        : {idx.max_bucket_size} "
              f"(cap {settings.max_bucket_size})")
        print(f"hash version          : {idx.hash_version}")

        reads_doc = (ROOT / "fixtures" / "reads_syn.txt").read_text()
        records = parse_fasta_records(reads_doc)

        _hr("2) QUERY each synthetic read (candidates, not alignments)")
        for i, (header, body) in enumerate(records, start=1):
            read = parse_sequence(body, name=f"read{i}").sequence
            qid = new_request_id()
            log_event(logger, step="demo_query", verdict="START",
                      identity=qid, run_id=RUN_ID, read_header=header.split()[0])
            try:
                q = svc.query_read(RUN_ID, read, k=K, w=W)
            except MiniseedError as exc:
                print(f"\n[{header.split()[0]}] -> ERROR {exc.code.value}: "
                      f"{exc.message}")
                log_event(logger, step="demo_query", verdict="ERROR",
                          identity=qid, run_id=RUN_ID, code=exc.code.value)
                continue
            print(f"\n[{header.split()[0]}] {header}")
            print(f"  read length={q.query_length} query_seeds="
                  f"{q.query_seed_count} total_hits={q.total_hits}")
            print(f"  candidate_is_alignment={q.candidate_is_alignment} "
                  f"({q.note})")
            for loc in q.locations[:5]:
                print(
                    f"  - ref[{loc.ref_start:>3}..{loc.ref_end:<3}] "
                    f"strand={loc.strand} diag={loc.diagonal:>4} "
                    f"hits={loc.hit_count} distinct_seeds="
                    f"{len(loc.hashes)}"
                )
            store.audit("demo_query", qid,
                        {"read": header.split()[0], "hits": q.total_hits},
                        run_id=RUN_ID)
            log_event(logger, step="demo_query", verdict="OK", identity=qid,
                      run_id=RUN_ID, hits=q.total_hits,
                      locations=len(q.locations))

        _hr("3) ERROR SEMANTICS walkthrough (explicit failure categories)")

        def show(label: str, fn) -> None:
            try:
                fn()
            except MiniseedError as exc:
                print(f"  {label:28} -> {exc.code.value:20} {exc.message}")
            else:
                print(f"  {label:28} -> UNEXPECTED SUCCESS (bug!)")

        show("invalid character 'X'",
             lambda: svc.index_reference("e1", "ACGTX", k=K, w=W))
        show("sequence too short",
             lambda: svc.query_read(RUN_ID, "ACGT", k=K, w=W))
        show("run not found",
             lambda: svc.query_read("nope", "ACGTACGTACGTAC", k=K, w=W))
        svc.index_reference("dup", ref[:30], k=K, w=W)
        show("run already exists",
             lambda: svc.index_reference("dup", ref[:30], k=K, w=W))
        show("parameter conflict (k)",
             lambda: svc.query_read(RUN_ID, "ACGTACGTACGTAC", k=11, w=W))
        show("invalid parameter (w=0)",
             lambda: svc.query_read(RUN_ID, "ACGTACGTACGTAC", k=K, w=0))

        _hr("4) PROVENANCE tail (audit events for the demo run)")
        for ev in store.list_audit(RUN_ID, limit=8):
            print(f"  {ev['at']}  {ev['event']:16} identity={ev['identity']} "
                  f"detail={ev['detail']}")

    print("\nDemo complete. Structured log: logs/demo.log "
          "SQLite DB: data/demo.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
