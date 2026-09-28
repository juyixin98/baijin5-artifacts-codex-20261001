"""CLI entry-point smoke tests."""

from __future__ import annotations

import sys

import pytest

from sparse_embeddings import __main__ as cli_module
from sparse_embeddings import __version__


def test_version_flag_exits_zero_and_prints_version(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sparse_embeddings", "--version"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_invokes_uvicorn_with_parsed_args(monkeypatch):
    captured: dict = {}

    def fake_run(target: str, **kwargs) -> None:
        captured["target"] = target
        captured.update(kwargs)

    monkeypatch.setattr(cli_module.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["sparse_embeddings", "--host", "0.0.0.0", "--port", "9999"]
    )
    cli_module.main()
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["target"] == "sparse_embeddings.api.app:app"
    assert captured["reload"] is False
