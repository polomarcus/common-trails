"""Database session factory.

Connection pool config is read from env vars at import time so prod
(Cloud SQL db-f1-micro, hard cap of 25 concurrent connections, 3
reserved for superuser, leaving 22 for clients) can run a tighter pool
than local dev. Defaults preserve the historical 20+10=30 behavior so
docker-compose runs unchanged.

For prod with max_instances >= 2:
  DB_POOL_SIZE=5
  DB_MAX_OVERFLOW=3
→ each backend uses at most 8 connections; 2 instances = 16 total,
   leaving ~6 for Cloud SQL proxy, admin sessions, and migrations.
"""
import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5487/common_trails",
)

# Pool sizing — see module docstring for the Cloud SQL db-f1-micro math
_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_recycle=_POOL_RECYCLE,
    pool_timeout=_POOL_TIMEOUT,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session]:
    """FastAPI dependency — yields a DB session and closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Returns True if database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
