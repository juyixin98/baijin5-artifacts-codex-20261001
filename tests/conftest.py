"""pytest 共享夹具。

* 把仓库根加入 sys.path;
* load_settings 指向临时数据库与临时日志目录, 绝不污染开发数据;
* 加载 tests/fixtures/expected_results.yaml 中手工编写的期望。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = ROOT / "samples"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONER__DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("REASONER__LOG_DIR", str(tmp_path / "logs"))
    return load_settings()


@pytest.fixture
def expected_cases():
    with open(FIXTURES / "expected_results.yaml", "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["cases"]


@pytest.fixture
def expected_error_cases():
    with open(FIXTURES / "expected_results.yaml", "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["error_cases"]


def sample_path(name: str) -> Path:
    return SAMPLES / name
