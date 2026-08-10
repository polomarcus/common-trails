"""Centralized logging configuration.

Call ``setup_logging()`` once at startup (in main.py) to configure
structured JSON logging for production and human-readable logs for dev.
"""
import logging
import os
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
