"""测试基础设施:结构化运行日志 + 共享夹具。

每次测试运行在 tests/artifacts/ 下生成一份 JSONL 日志:
- 头部记录 run_id、Python/phe/fastapi/cryptography/pytest 版本;
- 每个用例记录结果与耗时;
- 用例内通过 rlog 夹具记录输入、计算步骤与判定依据,
  使日志可关联到具体输入与运行身份。
"""
from __future__ import annotations

import json
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cryptography
import fastapi
import phe
import pytest

from app.config import Settings
from app.service import AggregationService

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

# 测试用固定 Fernet 密钥(仅测试,非生产秘密)
TEST_FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class RunLogger:
    def __init__(self, path: Path, run_id: str) -> None:
        self.run_id = run_id
        self._fh = path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        record.setdefault("run_id", self.run_id)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


@pytest.fixture(scope="session")
def run_logger():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ARTIFACTS_DIR / f"test_run_{stamp}_{run_id}.jsonl"
    logger = RunLogger(path, run_id)
    logger.write({
        "type": "header",
        "python": platform.python_version(),
        "phe": phe.__version__,
        "fastapi": fastapi.__version__,
        "cryptography": cryptography.__version__,
        "pytest": pytest.__version__,
    })
    yield logger
    logger.close()
    print(f"\n[run-log] {path}")


@pytest.fixture
def rlog(request, run_logger):
    """在用例内记录计算步骤: rlog('step-name', key=value, ...)"""
    def _log(step: str, **data):
        run_logger.write({
            "type": "step",
            "test": request.node.nodeid,
            "step": step,
            **data,
        })
    return _log


def pytest_runtest_logreport(report):
    """把每个用例的结果写入当前会话的 run 日志。"""
    if report.when != "call":
        return
    logger = getattr(pytest_runtest_logreport, "_logger", None)
    if logger is None:
        return
    logger.write({
        "type": "outcome",
        "test": report.nodeid,
        "outcome": report.outcome,
        "duration_s": round(report.duration, 6),
    })


@pytest.fixture(autouse=True)
def _attach_logger(run_logger):
    pytest_runtest_logreport._logger = run_logger


@pytest.fixture(scope="session")
def test_settings(tmp_path_factory):
    return Settings(
        db_path=str(tmp_path_factory.mktemp("db") / "test.db"),
        key_size=1024,               # 测试提速;服务默认 2048
        max_plaintext_abs=2**20,
        max_weight_abs=2**10,
        fernet_key=TEST_FERNET_KEY,
    )


@pytest.fixture()
def service(test_settings, tmp_path):
    """每个用例独立数据库,互不影响。"""
    settings = Settings(
        db_path=str(tmp_path / "svc.db"),
        key_size=test_settings.key_size,
        max_plaintext_abs=test_settings.max_plaintext_abs,
        max_weight_abs=test_settings.max_weight_abs,
        fernet_key=test_settings.fernet_key,
    )
    svc = AggregationService(settings)
    yield svc
    svc.store.close()


@pytest.fixture()
def client(service):
    from fastapi.testclient import TestClient
    from app.api import create_app
    return TestClient(create_app(service.settings, service=service))
