"""Configuration layer and module entry point tests."""
from __future__ import annotations

import runpy


def test_settings_env_overrides(monkeypatch, tmp_path):
    from miniseed.config import Settings

    db = tmp_path / "env.db"
    monkeypatch.setenv("MINISEED_K", "7")
    monkeypatch.setenv("MINISEED_W", "4")
    monkeypatch.setenv("MINISEED_MAX_BUCKET", "12")
    monkeypatch.setenv("MINISEED_MAX_CANDIDATES", "34")
    monkeypatch.setenv("MINISEED_DB", str(db))
    s = Settings.from_env()
    assert (s.k, s.w, s.max_bucket_size, s.max_candidates) == (7, 4, 12, 34)
    assert s.db_path == db


def test_settings_env_empty_falls_back_to_defaults(monkeypatch):
    from miniseed.config import Settings, DEFAULT_K, DEFAULT_W

    monkeypatch.setenv("MINISEED_K", "  ")
    monkeypatch.delenv("MINISEED_W", raising=False)
    s = Settings.from_env()
    assert s.k == DEFAULT_K
    assert s.w == DEFAULT_W


def test_module_entrypoint_invokes_uvicorn(monkeypatch):
    import miniseed.__main__ as entry

    calls = {}

    def fake_run(target, **kwargs):
        calls["target"] = target
        calls.update(kwargs)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    entry.main()
    assert calls["target"] == "miniseed.api:app"
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8000

    # ``python -m miniseed`` path also executes without import error.
    runpy.run_module("miniseed", run_name="not_main")


def test_store_context_manager(tmp_path):
    from miniseed.store import SeedStore

    path = tmp_path / "cm.db"
    with SeedStore(path) as store:
        assert store.list_runs() == []
    # Connection closed after the block -> operating on it raises.
    import sqlite3

    try:
        store.list_runs()
    except sqlite3.ProgrammingError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected closed connection")


def test_transaction_rolls_back_on_error(tmp_path):
    import pytest
    from miniseed.store import SeedStore

    path = tmp_path / "rb.db"
    with SeedStore(path) as store:
        with pytest.raises(RuntimeError, match="boom"):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO runs (run_id, k, w, hash_version, ref_name,"
                    " ref_length, seed_count) VALUES ('tx', 9, 5, 'v', 'r',"
                    " 10, 0)"
                )
                raise RuntimeError("boom")
        # The rolled-back row must not be visible.
        assert store.list_runs() == []
        # A subsequent clean transaction still works.
        store.audit("post_rollback", "id-rb", {"ok": True})
        assert {e["event"] for e in store.list_audit(limit=5)} == {
            "post_rollback"
        }
