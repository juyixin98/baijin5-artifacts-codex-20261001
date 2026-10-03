"""Configuration loading for the DTW service.

Configuration lives outside the package (config/default.yaml) so tests and
deployments can point at alternative files via the DTW_CONFIG environment
variable without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


@dataclass(frozen=True)
class DtwSettings:
    metric: str
    step_pattern: str
    max_consecutive_run: int
    sakoe_chiba_window: int | None
    normalization: str
    storage: str

    def resolved_window(self, n: int, m: int) -> int:
        """Concrete band half-width; an unconfigured window covers everything."""
        if self.sakoe_chiba_window is None:
            return max(n, m)
        return self.sakoe_chiba_window


@dataclass(frozen=True)
class StreamSettings:
    buffer_size: int
    snapshot_history: int


@dataclass(frozen=True)
class Settings:
    dtw: DtwSettings
    stream: StreamSettings


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    config_path = Path(path or os.environ.get("DTW_CONFIG", _DEFAULT_CONFIG))
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    dtw_raw = raw["dtw"]
    stream_raw = raw["stream"]

    if dtw_raw["metric"] != "euclidean":
        raise ValueError(f"unsupported metric: {dtw_raw['metric']!r}")
    if dtw_raw["step_pattern"] != "symmetric":
        raise ValueError(f"unsupported step pattern: {dtw_raw['step_pattern']!r}")
    if dtw_raw["normalization"] != "path_length":
        raise ValueError(f"unsupported normalization: {dtw_raw['normalization']!r}")
    if dtw_raw["max_consecutive_run"] < 1:
        raise ValueError("max_consecutive_run must be >= 1")
    window = dtw_raw["sakoe_chiba_window"]
    if window is not None and window < 0:
        raise ValueError("sakoe_chiba_window must be >= 0 or null")
    if dtw_raw["storage"] not in {"auto", "dense", "banded"}:
        raise ValueError(f"unsupported storage policy: {dtw_raw['storage']!r}")

    return Settings(
        dtw=DtwSettings(
            metric=dtw_raw["metric"],
            step_pattern=dtw_raw["step_pattern"],
            max_consecutive_run=int(dtw_raw["max_consecutive_run"]),
            sakoe_chiba_window=None if window is None else int(window),
            normalization=dtw_raw["normalization"],
            storage=dtw_raw["storage"],
        ),
        stream=StreamSettings(
            buffer_size=int(stream_raw["buffer_size"]),
            snapshot_history=int(stream_raw["snapshot_history"]),
        ),
    )
