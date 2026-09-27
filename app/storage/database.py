"""SQLite 连接与 schema 管理。

表结构:
  knowledge_bases  知识库 (一份规则集 + 优先关系 + 版本)
  facts            接地证据 (按 kb_id 分区, 规范键去重)
  rules            严格/可撤销规则 (kind 列物理分开)
  priorities       优先边 (strong_id > weak_id)
  runs             推理运行记录 (run_id/查询/结果/错误分类), 支持问题回放
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    kb_id        TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    stats_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS facts (
    kb_id      TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
    predicate  TEXT NOT NULL,
    sign       TEXT NOT NULL CHECK (sign IN ('+', '-')),
    args_json  TEXT NOT NULL,
    text       TEXT NOT NULL,
    PRIMARY KEY (kb_id, predicate, sign, args_json)
);

CREATE TABLE IF NOT EXISTS rules (
    kb_id       TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
    rule_id     TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('strict', 'defeasible')),
    head_json   TEXT NOT NULL,
    body_json   TEXT NOT NULL,
    rule_text   TEXT NOT NULL,
    line        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kb_id, rule_id)
);

CREATE TABLE IF NOT EXISTS priorities (
    kb_id     TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
    strong_id TEXT NOT NULL,
    weak_id   TEXT NOT NULL,
    PRIMARY KEY (kb_id, strong_id, weak_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    kb_id           TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    endpoint        TEXT NOT NULL,
    request_json    TEXT NOT NULL,
    status_summary  TEXT,
    result_json     TEXT,
    error_category  TEXT,
    error_name      TEXT,
    error_message   TEXT,
    details_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_rules_kb ON rules(kb_id);
CREATE INDEX IF NOT EXISTS idx_facts_kb ON facts(kb_id);
CREATE INDEX IF NOT EXISTS idx_priorities_kb ON priorities(kb_id);
CREATE INDEX IF NOT EXISTS idx_runs_kb ON runs(kb_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
