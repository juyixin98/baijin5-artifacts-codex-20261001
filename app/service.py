"""Application service: orchestrates corpus, index, mining kernel and storage."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter

from . import ENGINE_VERSION
from .config import Settings
from .corpus import Transaction, normalise_transactions
from .logging_setup import get_logger
from .miner import (
    Itemset,
    StackFrame,
    derive_maximal,
    initialise_search,
    mine_chunk,
    sort_itemsets,
)
from .models import DatasetStats
from .repository import JobRecord, Repository
from .vertical_index import build_vertical_index

log = get_logger()


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


def _content_hash(transactions: list[Transaction]) -> str:
    canonical = json.dumps(
        [[txn.tid, txn.items] for txn in transactions],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_stats(transactions: list[Transaction]) -> DatasetStats:
    itemset_signatures = Counter(txn.items for txn in transactions)
    # Transactions participating in a duplicated (identical) group; each copy
    # keeps its own tid/identity -- this number is informational only.
    duplicate_count = sum(c for c in itemset_signatures.values() if c > 1)
    distinct_items = {item for txn in transactions for item in txn.items}
    return DatasetStats(
        transaction_count=len(transactions),
        distinct_item_count=len(distinct_items),
        empty_transaction_count=sum(1 for txn in transactions if not txn.items),
        duplicate_transaction_count=duplicate_count,
    )


class FimService:
    def __init__(self, repository: Repository, settings: Settings):
        self.repo = repository
        self.settings = settings

    # -- ingestion ----------------------------------------------------------

    def ingest_dataset(self, name: str | None, raw_rows: list[dict]) -> tuple[str, DatasetStats]:
        transactions = normalise_transactions(raw_rows)
        stats = compute_stats(transactions)
        dataset_id = uuid.uuid4().hex
        content_hash = _content_hash(transactions)
        self.repo.insert_dataset(
            dataset_id=dataset_id,
            name=name,
            content_hash=content_hash,
            engine_version=ENGINE_VERSION,
            transactions=transactions,
            stats=stats.model_dump(),
        )
        log.info(
            "ingested dataset %s (%d transactions, %d distinct items, %d empty, %d duplicate copies)",
            dataset_id,
            stats.transaction_count,
            stats.distinct_item_count,
            stats.empty_transaction_count,
            stats.duplicate_transaction_count,
        )
        return dataset_id, stats

    def get_dataset_or_raise(self, dataset_id: str):
        record = self.repo.get_dataset(dataset_id)
        if record is None:
            raise NotFoundError(f"dataset {dataset_id!r} does not exist")
        return record

    # -- mining -------------------------------------------------------------

    def _load_index(self, dataset_id: str):
        transactions = self.repo.load_transactions(dataset_id)
        return build_vertical_index(transactions)

    def _clamp_budget(self, requested: int | None, notes: list[str]) -> int:
        budget = self.settings.default_budget_nodes if requested is None else requested
        if budget > self.settings.max_budget_nodes:
            notes.append(
                f"requested budget {budget} exceeds configured maximum "
                f"{self.settings.max_budget_nodes}; clamped"
            )
            budget = self.settings.max_budget_nodes
        return budget

    def _persist(self, record: JobRecord) -> None:
        self.repo.upsert_job(record)

    def create_job(
        self,
        dataset_id: str,
        min_support: int,
        budget: int | None,
        request_id: str,
    ) -> tuple[JobRecord, int]:
        dataset = self.get_dataset_or_raise(dataset_id)
        notes: list[str] = []
        budget = self._clamp_budget(budget, notes)

        index = self._load_index(dataset_id)
        frames, root_outcome = initialise_search(index, min_support)

        closed: dict[Itemset, int] = dict(root_outcome.closed_new)
        evaluations = root_outcome.evaluations_used
        complete = root_outcome.complete
        chunk_evals = 0
        if not root_outcome.root_closed:
            notes.append(
                "empty itemset is not closed: at least one item appears in every transaction"
            )
        if complete:
            notes.append(
                f"min_support={min_support} exceeds transaction count {index.transaction_count}; "
                "no frequent itemset exists (search complete)"
            )

        if not complete and budget > 0:
            outcome = mine_chunk(index, min_support, budget, frames)
            closed.update(outcome.closed_new)
            evaluations += outcome.evaluations_used
            chunk_evals = outcome.evaluations_used
            complete = outcome.complete
        elif not complete:
            notes.append("budget=0: only the root node was evaluated; resume to continue")

        if not complete:
            notes.append(
                "PARTIAL result: enumeration budget exhausted; maximal itemsets are unconfirmed "
                "until the search completes -- POST /jobs/{job_id}/resume to continue"
            )

        record = JobRecord(
            job_id=uuid.uuid4().hex,
            request_id=request_id,
            dataset_id=dataset_id,
            dataset_hash=dataset.content_hash,
            engine_version=ENGINE_VERSION,
            min_support=min_support,
            status="complete" if complete else "running",
            complete=complete,
            evaluations_used=evaluations,
            frames=frames,
            closed=closed,
            notes=notes,
        )
        self._persist(record)
        log.info(
            "created job %s dataset=%s min_support=%d evals=%d closed=%d complete=%s",
            record.job_id,
            dataset_id,
            min_support,
            evaluations,
            len(closed),
            complete,
        )
        return record, chunk_evals

    def resume_job(self, job_id: str, budget: int | None, request_id: str) -> tuple[JobRecord, int]:
        record = self.repo.get_job(job_id)
        if record is None:
            raise NotFoundError(f"job {job_id!r} does not exist")
        if record.complete:
            log.info("resume requested for already-complete job %s; no work performed", job_id)
            return record, 0

        notes: list[str] = []
        budget = self._clamp_budget(budget, notes)
        index = self._load_index(record.dataset_id)
        frames = list(record.frames)
        outcome = mine_chunk(index, record.min_support, budget, frames)

        closed = dict(record.closed)
        closed.update(outcome.closed_new)
        evaluations = record.evaluations_used + outcome.evaluations_used
        complete = outcome.complete
        if not complete:
            notes.append(
                "PARTIAL result: chunk budget exhausted; maximal itemsets remain unconfirmed; "
                "resume again to continue"
            )

        new_record = JobRecord(
            job_id=record.job_id,
            request_id=request_id,
            dataset_id=record.dataset_id,
            dataset_hash=record.dataset_hash,
            engine_version=ENGINE_VERSION,
            min_support=record.min_support,
            status="complete" if complete else "running",
            complete=complete,
            evaluations_used=evaluations,
            frames=frames,
            closed=closed,
            notes=notes,
        )
        self._persist(new_record)
        log.info(
            "resumed job %s chunk_evals=%d total_evals=%d closed=%d complete=%s",
            job_id,
            outcome.evaluations_used,
            evaluations,
            len(closed),
            complete,
        )
        return new_record, outcome.evaluations_used

    def get_job_or_raise(self, job_id: str) -> JobRecord:
        record = self.repo.get_job(job_id)
        if record is None:
            raise NotFoundError(f"job {job_id!r} does not exist")
        return record


def serialise_job(record: JobRecord, chunk_evals: int | None = None) -> dict:
    """Build the JSON payload; maximal certainty is flagged explicitly."""
    maximals = derive_maximal(record.closed) if record.complete else []
    closed_sorted = sort_itemsets(record.closed.keys())
    maximal_sorted = sort_itemsets(maximals)
    chunk = None if chunk_evals is None else {
        "evaluations_in_chunk": chunk_evals,
        "resumed_with_request_id": record.request_id,
    }
    payload = {
        "job_id": record.job_id,
        "request_id": record.request_id,
        "dataset_id": record.dataset_id,
        "dataset_hash": record.dataset_hash[:12],
        "engine_version": record.engine_version,
        "min_support": record.min_support,
        "status": record.status,
        "complete": record.complete,
        "evaluations_used": record.evaluations_used,
        "closed_itemsets": [
            {"itemset": items, "support": record.closed[frozenset(items)]}
            for items in closed_sorted
        ],
        "maximal_itemsets": [
            {"itemset": items, "support": record.closed[frozenset(items)]}
            for items in maximal_sorted
        ],
        "maximal_results_certain": record.complete,
        "notes": list(record.notes),
    }
    if chunk is not None:
        payload["chunk"] = chunk
    return payload
