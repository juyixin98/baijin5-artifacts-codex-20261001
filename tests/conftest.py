"""Shared pytest fixtures, including the convergence recorder.

Convergence records (engine, fixture, iterations / queue pops, changed
pixels) are appended to artifacts/convergence.jsonl so convergence
behavior is reviewed as data, not eyeballed from images.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ARTIFACTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "artifacts"


class ConvergenceLog:
    def __init__(self, path: pathlib.Path):
        self._path = path

    def record(self, **fields) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, sort_keys=True) + "\n")


@pytest.fixture(scope="session")
def convergence_log() -> ConvergenceLog:
    path = ARTIFACTS_DIR / "convergence.jsonl"
    if path.exists():
        path.unlink()
    return ConvergenceLog(path)
