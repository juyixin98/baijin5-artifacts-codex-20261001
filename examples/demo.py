"""End-to-end demo: ingest synthetic records, set constraints, lock a
mapping, resolve twice, and show affected entities. Runs against the
service layer directly (no server needed).

Usage: python3 examples/demo.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from er_backend.config import Settings
from er_backend.index.store import Store
from er_backend.models import ConstraintSet, Lock, Record
from er_backend.service import ResolutionService

TOKEN_MAP = json.loads(
    (Path(__file__).parent / "token_map.json").read_text(encoding="utf-8")
)

RECORDS = [
    Record(record_id="A", name="Acme Trading"),
    Record(record_id="B", name="Acme Trading Ltd"),
    Record(record_id="C", name="Acme Trading Company"),
    Record(record_id="F", name="北京星辰科技有限公司"),
    Record(record_id="G", name="Beijing Xingchen Technology Ltd"),
    Record(record_id="H", name="上海星辰科技有限公司"),
]


def show(title: str, result) -> None:
    print(f"\n== {title} (run {result.run_id}) ==")
    for cluster in result.clusters:
        locks = f" locks={cluster.lock_ids}" if cluster.lock_ids else ""
        print(f"  {cluster.cluster_id}: {cluster.record_ids}{locks}")
        for line in result.evidence[cluster.cluster_id]:
            print(f"      evidence: {line}")
    print(f"  affected: {result.affected_record_ids}")


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "demo.sqlite3"
    service = ResolutionService(Store(db), Settings(db_path=str(db)), TOKEN_MAP)

    service.ingest(RECORDS)
    # A-B must link, A-C cannot link: pairwise similarity is not transitive.
    service.set_constraints(
        ConstraintSet(must_link=[("A", "B")], cannot_link=[("A", "C")])
    )
    show("initial resolve", service.resolve())

    # Human confirms F and G are the same entity: lock the mapping.
    service.add_lock(Lock(lock_id="L-human-1", record_ids=["F", "G"]))
    show("after human lock F~G", service.resolve())

    replay = service.replay(service.store.latest_run_id())
    print(f"\nreplay check: {replay}")
    print(f"run journal persisted at: {db}")


if __name__ == "__main__":
    main()
