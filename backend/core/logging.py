"""Structured JSON logging with request correlation ids. Secrets are never logged."""
from __future__ import annotations
import json, logging, sys, contextvars
from datetime import datetime, timezone

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_REDACT = ("password", "token", "secret", "authorization", "api_key")


class JsonFormatter(logging.Formatter):
    def format(self, rec: logging.LogRecord) -> str:
        out = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": rec.levelname,
               "logger": rec.name, "msg": rec.getMessage(), "request_id": request_id.get()}
        for k, v in getattr(rec, "fields", {}).items():
            out[k] = "[REDACTED]" if any(r in k.lower() for r in _REDACT) else v
        if rec.exc_info:
            out["exc"] = self.formatException(rec.exc_info)
        return json.dumps(out, default=str)


def setup(level="INFO"):
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


def event(logger: logging.Logger, msg: str, **fields):
    logger.info(msg, extra={"fields": fields})
