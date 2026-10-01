"""Storage-level tests: SQLite roundtrip and concurrent writes."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.api.storage import RunStore
from app.core.contracts import Settings
from app.core.service import run_analysis

ROOT = Path(__file__).resolve().parents[2]


def _result(seed_name: str):
    payload = json.loads((ROOT / "data" / "sample" / "balanced.json").read_text())
    payload["name"] = seed_name
    return run_analysis(payload, Settings())


@pytest.mark.integration
def test_save_and_get_roundtrip(tmp_path):
    store = RunStore(tmp_path / "r.db")
    result = _result("exp-a")
    store.save(result)
    fetched = store.get(result.run_id)
    assert fetched["run_id"] == result.run_id
    assert fetched["cuped"]["estimate"] == result.cuped.estimate
    store.close()


@pytest.mark.integration
def test_concurrent_saves_do_not_corrupt(tmp_path):
    store = RunStore(tmp_path / "r.db")
    results = [_result(f"exp-{i}") for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(store.save, results))
    listed = store.list_runs(limit=100)
    assert len(listed) == 20
    # Every run retrievable and intact.
    for result in results:
        fetched = store.get(result.run_id)
        assert fetched["data_fingerprint"] == result.data_fingerprint
    store.close()


@pytest.mark.integration
def test_get_missing_run_is_typed(tmp_path):
    from app.core.contracts import ErrorCode, EstimationError
    store = RunStore(tmp_path / "r.db")
    with pytest.raises(EstimationError) as exc:
        store.get("nope")
    assert exc.value.code is ErrorCode.RUN_NOT_FOUND
    store.close()
