"""结构化日志：统一 JSON 行输出，便于采集与排查。

P0 目标：让路由、检索、SQL、诊断等关键步骤留下可检索的日志，
包括 intent / thread_id / 耗时 / 关键参数，避免只靠 LLM 黑盒输出。
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar

from app.config import settings

_thread_id: ContextVar[str] = ContextVar("thread_id", default="-")


def set_thread_id(tid: str) -> None:
    _thread_id.set(tid or "-")


def get_thread_id() -> str:
    return _thread_id.get()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "thread_id": get_thread_id(),
            "msg": record.getMessage(),
        }
        if getattr(record, "extra_fields", None):
            data.update(record.extra_fields)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
_logger = logging.getLogger("glass_agent")
_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
_logger.propagate = False
if not _logger.handlers:
    _logger.addHandler(_handler)


def log(level: str, event: str, **fields) -> None:
    """记录一条结构化日志：log("info", "router.decide", intent="qa", cost_ms=12)。"""
    rec = _logger.makeRecord(
        _logger.name, getattr(logging, level.upper(), logging.INFO),
        "(unknown)", 0, event, None, None,
    )
    rec.extra_fields = fields
    _logger.handle(rec)
