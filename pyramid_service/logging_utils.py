"""结构化 JSON 日志。

每条日志携带运行编号（run_id）与关键中间状态字段，事件名固定，
便于按运行编号回放问题、按事件名检索判断理由。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

LOGGER_NAME = "pyramid_service"


class JsonFormatter(logging.Formatter):
    RESERVED = ("run_id", "event")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 6),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logger(log_file: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = JsonFormatter()
    if not logger.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    if log_file is not None:
        path = str(log_file)
        if not any(
            isinstance(h, logging.FileHandler) and h.baseFilename == path
            for h in logger.handlers
        ):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(path)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
    return logger


def log_event(logger: logging.Logger, event: str, message: str = "", **fields) -> None:
    """记录一条结构化事件；fields 中应包含 run_id 与判断理由等中间状态。"""
    logger.info(message or event, extra={"event": event, "fields": fields})


def new_run_id() -> str:
    import uuid

    return f"run-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
