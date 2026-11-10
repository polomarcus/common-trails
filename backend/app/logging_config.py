"""Centralized logging configuration.

Call ``setup_logging()`` once at startup (in main.py) to configure
structured JSON logging for production and human-readable logs for dev.
"""
import logging
import os
import re
import sys


def setup_logging() -> None:
    """Configure the root logger based on environment variables.

    ENV=production → JSON-formatted lines (for Cloud Logging / Sentry).
    Otherwise        → human-readable ``[level] name: message`` format.

    LOG_LEVEL env var controls verbosity (default: INFO).
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    env = os.environ.get("ENV", "development")

    if env == "production":
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
    else:
        fmt = "[%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stdout,
        force=True,  # override any previous basicConfig
    )

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Shared "Ma heatmap" links: the token in /s/<token>/… IS the credential.
    # slowapi logs the request path when a limit is hit (INFO), uvicorn's
    # access log prints it — redact it in both.
    for name in ("slowapi", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, ShareTokenRedactor) for f in lg.filters):
            lg.addFilter(ShareTokenRedactor())


_SHARE_TOKEN_RE = re.compile(r"/s/[A-Za-z0-9_-]{16,}")


class ShareTokenRedactor(logging.Filter):
    """Rewrite ``/s/<token>`` as ``/s/[token]`` in a log record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — never drop a record
            return True
        if "/s/" in msg:
            record.msg = _SHARE_TOKEN_RE.sub("/s/[token]", msg)
            record.args = ()
        return True
