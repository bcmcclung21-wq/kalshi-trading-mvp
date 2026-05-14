from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = (
            "cycle_id",
            "fetch_duration_ms",
            "refresh_duration_ms",
            "raw_count",
            "active_count",
            "orderbook_count",
            "error_flags",
            "active_markets",
            "processing_latency_p99",
        )
        for field in extra_fields:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
