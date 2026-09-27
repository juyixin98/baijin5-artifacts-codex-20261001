"""独立配置加载。

读取 config/default.yaml, 环境变量覆盖:
    REASONER__DB_PATH        覆盖 storage.db_path
    REASONER__LOG_DIR        覆盖日志目录前缀
    REASONER__HOST / REASONER__PORT
    REASONER__MAX_GROUND_INSTANCES
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "default.yaml"


@dataclass(frozen=True)
class KernelLimits:
    max_chains_per_goal: int = 64
    max_ground_instances: int = 20000
    max_arguments: int = 50000
    fixpoint_iterations: int = 512
    max_input_facts: int = 10000
    max_input_rules: int = 5000


@dataclass(frozen=True)
class Settings:
    root: Path
    host: str
    port: int
    db_path: Path
    request_log: Path
    kernel_trace: Path
    error_log: Path
    test_db_path: Path
    limits: KernelLimits = field(default_factory=KernelLimits)

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path,
            self.test_db_path,
            self.request_log,
            self.kernel_trace,
            self.error_log,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_settings(config_path: str | Path | None = None) -> Settings:
    config_file = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(config_file, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    root = _PROJECT_ROOT
    log_dir_override = os.environ.get("REASONER__LOG_DIR")

    def resolve_log(name: str) -> Path:
        if log_dir_override:
            return Path(log_dir_override) / Path(name).name
        return _resolve(root, name)

    db_path = os.environ.get("REASONER__DB_PATH") or raw["storage"]["db_path"]
    host = os.environ.get("REASONER__HOST", raw["server"]["host"])
    port = int(os.environ.get("REASONER__PORT", raw["server"]["port"]))

    kernel_raw = raw.get("kernel", {})
    limits = KernelLimits(
        max_chains_per_goal=int(kernel_raw.get("max_chains_per_goal", 64)),
        max_ground_instances=int(
            os.environ.get(
                "REASONER__MAX_GROUND_INSTANCES",
                kernel_raw.get("max_ground_instances", 20000),
            )
        ),
        fixpoint_iterations=int(kernel_raw.get("fixpoint_iterations", 512)),
        max_input_facts=int(kernel_raw.get("max_input_facts", 10000)),
        max_input_rules=int(kernel_raw.get("max_input_rules", 5000)),
    )

    settings = Settings(
        root=root,
        host=host,
        port=port,
        db_path=_resolve(root, str(db_path)),
        request_log=resolve_log(raw["logging"]["request_log"]),
        kernel_trace=resolve_log(raw["logging"]["kernel_trace"]),
        error_log=resolve_log(raw["logging"]["error_log"]),
        test_db_path=_resolve(root, raw.get("testing", {}).get("db_path", "data/test.db")),
        limits=limits,
    )
    settings.ensure_dirs()
    return settings
