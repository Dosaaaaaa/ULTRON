"""Structured JSON logging with credential redaction.

Message bodies (user prompts, model replies) are never logged by the application.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\",}]{6,}"),
]
_REDACTED = "[REDACTED]"
_known_secrets: set[str] = set()


def register_secrets(values: list[str]) -> None:
    """Exact-match redaction for configured secrets (belt and braces beside the patterns)."""
    _known_secrets.update(v for v in values if v and len(v) >= 8)


def redact(text: str) -> str:
    for secret in _known_secrets:
        text = text.replace(secret, _REDACTED)
    text = _PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    for pat in _PATTERNS[:3]:
        text = pat.sub(_REDACTED, text)
    return text


_STD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's access log is replaced by our request middleware (no headers, no bodies)
    logging.getLogger("uvicorn.access").disabled = True
    # These libraries log URLs, SQL and bound parameters (i.e. message bodies) at INFO/DEBUG.
    # Pin them regardless of the application's level.
    for noisy in ("httpx", "httpcore", "aiosqlite", "sqlalchemy.engine", "asyncio", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
