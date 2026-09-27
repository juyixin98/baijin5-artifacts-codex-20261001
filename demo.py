#!/usr/bin/env python3
"""Local end-to-end demonstration.

Runs the full HTTP stack in-process (via Starlette's TestClient) against a
throwaway SQLite database, so no server process or external services are
needed:

    python3 demo.py

Demonstrates:
  1. ingestion with repeated items / duplicate / empty transactions;
  2. a budgeted job that returns a PARTIAL result;
  3. resume-until-complete with per-chunk progress;
  4. the closed vs maximal distinction;
  5. explicit error categories (422 INVALID_CORPUS, 404 *NOT_FOUND).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.logging_setup import configure_logging
from app.repository import Repository
from app.service import FimService

DEMO_CORPUS = [
    {"tid": "t1", "items": ["a", "b", "b"]},          # repeated item -> counted once
    {"tid": "t2", "items": ["a", "b", "c"]},
    {"tid": "t3", "items": ["a", "c"]},
    {"tid": "t4", "items": ["b", "c"]},
    {"tid": "t5", "items": ["a", "b", "c"]},          # same content as t2, own identity
    {"tid": "t6", "items": []},                        # empty transaction
]


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def show_error(response) -> None:
    body = response.json()
    print(
        f"HTTP {response.status_code} error_code={body['error_code']} "
        f"request_id={body['request_id']}\n  message: {body['message']}"
    )


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="fim-demo-")
    settings = Settings(
        database_path=str(Path(tmpdir) / "demo.db"),
        default_budget_nodes=10_000,
        max_budget_nodes=1_000_000,
        log_level="WARNING",
    )
    configure_logging("WARNING")
    service = FimService(Repository(settings.database_path), settings)
    client = TestClient(create_app(service, log_level="WARNING"))

    banner("health / version")
    health = client.get("/health", headers={"X-Request-ID": "demo-health"}).json()
    print(json.dumps(health, indent=2))

    banner("step 1: ingest corpus (note stats)")
    created = client.post(
        "/datasets",
        json={"name": "demo", "transactions": DEMO_CORPUS},
        headers={"X-Request-ID": "demo-ingest"},
    )
    created.raise_for_status()
    dataset = created.json()
    print(json.dumps(dataset, indent=2, ensure_ascii=False))
    dataset_id = dataset["dataset_id"]

    banner("step 2: create job with budget=2 -> expect PARTIAL result")
    job = client.post(
        "/jobs",
        json={"dataset_id": dataset_id, "min_support": 2, "budget": 2},
        headers={"X-Request-ID": "demo-job-create"},
    ).json()
    print(
        f"job_id={job['job_id']} request_id={job['request_id']} "
        f"status={job['status']} complete={job['complete']} "
        f"evaluations_used={job['evaluations_used']} "
        f"maximal_results_certain={job['maximal_results_certain']}"
    )
    print(f"closed so far: {len(job['closed_itemsets'])}; maximals withheld while uncertain")
    for note in job["notes"]:
        print(f"  NOTE: {note}")

    banner("step 3: resume until complete")
    while not job["complete"]:
        job = client.post(
            f"/jobs/{job['job_id']}/resume",
            json={"budget": 3},
            headers={"X-Request-ID": "demo-resume"},
        ).json()
        print(
            f"  chunk_evals={job['chunk']['evaluations_in_chunk']} "
            f"total_evals={job['evaluations_used']} closed={len(job['closed_itemsets'])} "
            f"complete={job['complete']}"
        )

    banner("step 4: final results — closed vs maximal (min_support=2)")
    print("CLOSED frequent itemsets (no same-support superset):")
    for entry in job["closed_itemsets"]:
        print(f"  support={entry['support']}  {entry['itemset']}")
    print("MAXIMAL frequent itemsets (no frequent superset):")
    for entry in job["maximal_itemsets"]:
        print(f"  support={entry['support']}  {entry['itemset']}")
    print(
        f"=> {len(job['closed_itemsets'])} closed vs "
        f"{len(job['maximal_itemsets'])} maximal; maximal is the stricter notion."
    )

    banner("step 5: error semantics")
    print("duplicate tids in one batch:")
    bad = client.post(
        "/datasets",
        json={"transactions": [
            {"tid": "dup", "items": ["a"]},
            {"tid": "dup", "items": ["b"]},
        ]},
    )
    show_error(bad)
    print("mining an unknown dataset:")
    show_error(client.post("/jobs", json={"dataset_id": "unknown", "min_support": 1}))
    print("resuming an unknown job:")
    show_error(client.post("/jobs/unknown/resume", json={}))

    banner(f"done — database left at {settings.database_path}")


if __name__ == "__main__":
    main()
