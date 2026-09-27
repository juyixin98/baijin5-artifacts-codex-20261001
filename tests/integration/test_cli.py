"""CLI entry-point tests (load-fixture against a temp on-disk database)."""

from __future__ import annotations

import json

from defeasible import cli
from defeasible.config import Settings
from defeasible.storage import EvidenceStore


def test_load_fixture_command_populates_store(tmp_path, monkeypatch) -> None:
    db = tmp_path / "case.db"
    fixture = tmp_path / "birds.json"
    fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "birds",
                        "description": "tiny",
                        "theory": {
                            "rules": [
                                {"id": "r", "kind": "default",
                                 "body": ["bird(X)"], "head": "flies(X)"}
                            ],
                            "priorities": [],
                        },
                        "evidence": ["bird(tweety)"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEFEASIBLE_DB_PATH", str(db))
    monkeypatch.setenv("DEFEASIBLE_LOG_PATH", str(tmp_path / "runs.jsonl"))

    rc = cli.main(["load-fixture", str(fixture)])
    assert rc == 0

    store = EvidenceStore(str(db))
    assert [c["case_id"] for c in store.list_cases()] == ["birds"]
    assert len(store.load_theory("birds")["rules"]) == 1
    assert [t.literal for t in store.load_evidence("birds")] == ["bird(tweety)"]
    store.close()


def test_cyclic_fixture_is_rejected_with_exit_code_2(tmp_path, monkeypatch, capsys) -> None:
    db = tmp_path / "c.db"
    fixture = tmp_path / "cyc.json"
    fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "cyc",
                        "theory": {
                            "rules": [
                                {"id": "a", "kind": "default",
                                 "body": ["p(X)"], "head": "q(X)"},
                                {"id": "b", "kind": "default",
                                 "body": ["r(X)"], "head": "-q(X)"},
                            ],
                            "priorities": [
                                {"higher": "a", "lower": "b"},
                                {"higher": "b", "lower": "a"},
                            ],
                        },
                        "evidence": ["p(t)", "r(t)"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEFEASIBLE_DB_PATH", str(db))
    monkeypatch.setenv("DEFEASIBLE_LOG_PATH", str(tmp_path / "runs.jsonl"))

    assert cli.main(["load-fixture", str(fixture)]) == 2
    assert "state_conflict" in capsys.readouterr().err


def test_settings_engine_limits_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("DEFEASIBLE_MAX_GROUND_RULES", "123")
    monkeypatch.setenv("DEFEASIBLE_PORT", "9999")
    s = Settings.from_env()
    assert s.port == 9999
    assert s.engine_limits().max_ground_rules == 123


def test_settings_rejects_non_positive_int(monkeypatch) -> None:
    monkeypatch.setenv("DEFEASIBLE_MAX_ROUNDS", "0")
    try:
        Settings.from_env()
    except ValueError:
        return
    raise AssertionError("non-positive limit should be rejected")


def test_settings_rejects_non_integer(monkeypatch) -> None:
    monkeypatch.setenv("DEFEASIBLE_MAX_CHAINS", "lots")
    try:
        Settings.from_env()
    except ValueError:
        return
    raise AssertionError("non-integer limit should be rejected")
