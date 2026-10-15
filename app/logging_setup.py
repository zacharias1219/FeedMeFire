"""Structured JSON logs. Request metadata only: no bodies, queries, cookies or content (PRD §14.3)."""
from __future__ import annotations

import json
import logging

_EXTRA = ("request_id", "method", "path", "status", "duration_ms", "job_id", "reason")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage()}
        for k in _EXTRA:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        return json.dumps(out)


def setup() -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root.handlers = [h]
    root.setLevel(logging.INFO)
