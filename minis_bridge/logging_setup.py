"""Structured logging with secret redaction.

The access log is the most likely place for a bearer token to leak (it used
to be part of the query string of every request), so every record passes
through :func:`redact` before it is written.
"""
import json
import logging
import os
import re
import sys
import time

LOGGER_NAME = "minis_bridge"

_PATTERNS = [
    re.compile(r"(?i)(token=)([A-Za-z0-9_\-]{4,})"),
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(bearer\s+)?([A-Za-z0-9._\-]{4,})"),
    re.compile(r"(?i)(x-api-key\"?\s*[:=]\s*\"?)([A-Za-z0-9._\-]{4,})"),
    re.compile(r"(?i)(client_secret\"?\s*[:=]\s*\"?)([A-Za-z0-9._\-]{4,})"),
]


def redact(text):
    if not text:
        return text
    s = str(text)
    s = _PATTERNS[0].sub(r"\1<redacted>", s)
    s = _PATTERNS[1].sub(lambda m: m.group(1) + ("bearer " if m.group(2) else "") + "<redacted>", s)
    s = _PATTERNS[2].sub(r"\1<redacted>", s)
    s = _PATTERNS[3].sub(r"\1<redacted>", s)
    return s


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "msg": redact(record.getMessage()),
            "logger": record.name,
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            for k, v in extra.items():
                payload[k] = redact(v) if isinstance(v, str) else v
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup(level="INFO", log_file=None):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = JsonFormatter()
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass
    return logger


def get(name=None):
    return logging.getLogger(LOGGER_NAME)


def event(logger, msg, **fields):
    logger.info(msg, extra={"extra_fields": fields})
