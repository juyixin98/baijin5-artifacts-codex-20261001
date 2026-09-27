"""Pytest configuration: correlation logging and fixture helpers.

Every test run writes a structured log file under ``tests/logs/`` whose lines
carry the pytest node id (input identity) and, from the engine itself,
session/run ids, versions, progress (cycle numbers) and decisions.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rete import __version__ as ENGINE_VERSION  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_RUN_ID = f"testrun-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
_LOG_FILE = LOG_DIR / f"{_RUN_ID}.log"


class _NodeFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.node = "?"

    def filter(self, record):
        record.node = self.node
        record.test_run = _RUN_ID
        return True


_node_filter = _NodeFilter()
_fmt = logging.Formatter(
    "%(asctime)s %(levelname)s [run=%(test_run)s node=%(node)s] "
    "%(name)s: %(message)s")

_file_handler = logging.FileHandler(_LOG_FILE, mode="w")
_file_handler.setFormatter(_fmt)
_file_handler.addFilter(_node_filter)
_stream_handler = logging.StreamHandler(sys.stderr)
_stream_handler.setFormatter(_fmt)
_stream_handler.addFilter(_node_filter)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(_file_handler)
_root.addHandler(_stream_handler)

log = logging.getLogger("conftest")


def pytest_configure(config):
    log.info("test run starting run_id=%s engine_version=%s python=%s "
             "pytest=%s log_file=%s",
             _RUN_ID, ENGINE_VERSION, sys.version.split()[0],
             pytest.__version__, _LOG_FILE)


def pytest_sessionfinish(session, exitstatus):
    log.info("test run finished run_id=%s exitstatus=%s", _RUN_ID, exitstatus)


@pytest.fixture(autouse=True)
def _correlate(request):
    _node_filter.node = request.node.nodeid
    log.info("test start: %s", request.node.nodeid)
    yield
    log.info("test end:   %s", request.node.nodeid)


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def build_engine_from_fixture(fixture: dict, store=None):
    """Create an engine, register the fixture rules and insert its facts.

    Returns (engine, [wme...]). Rules are registered BEFORE facts so the
    normal right-activation path is exercised.
    """
    from rete import Engine
    engine = Engine(store=store)
    log.info("build engine session=%s rules=%s", engine.session_id,
             [r["name"] for r in fixture["rules"]])
    for rule in fixture["rules"]:
        engine.add_rule(rule)
    wmes = []
    for step in fixture.get("facts", []):
        assert step["op"] == "insert"
        wmes.append(engine.insert(step["kind"], tuple(step["fields"])))
    return engine, wmes


def normalize_engine_matches(matches: list[dict]) -> set[tuple]:
    return {
        (m["rule"],
         tuple(tuple(k) for k in m["fact_keys"]),
         tuple(sorted(m["bindings"].items())))
        for m in matches
    }


def normalize_reference(ref: set[tuple]) -> set[tuple]:
    # reference combos are (kind, f1, ...) tuples — same normal form.
    return {(rule, combo, binds) for rule, combo, binds in ref}


def assert_match_sets_equal(engine_matches, reference_set, context=""):
    """Assert with a full diff: missing/extra matches are the verdict basis."""
    got = normalize_engine_matches(engine_matches)
    want = normalize_reference(reference_set)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing or extra:
        log.error("match mismatch context=%s missing=%d extra=%d",
                  context, len(missing), len(extra))
        for m in missing:
            log.error("MISSING (in reference, not engine): %s", m)
        for e in extra:
            log.error("EXTRA   (in engine, not reference): %s", e)
    assert not missing, f"{context}: engine missing {missing}"
    assert not extra, f"{context}: engine produced extra {extra}"


def distinct_fact_keys(engine) -> list[tuple]:
    return [(w.kind, *w.fields) for w in engine.facts.distinct_facts()]
