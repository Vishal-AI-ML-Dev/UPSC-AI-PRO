"""Database engine/session setup (SQLAlchemy 2.0).

Multi-user data layer. Uses DATABASE_URL from settings:
  - Production: managed Postgres (Neon/Supabase), e.g.
      postgresql://user:pass@host/db?sslmode=require
  - Local fallback: SQLite file (no setup needed)
Swap DBs by changing DATABASE_URL only - no code change.
"""

import logging
import pathlib

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.core.config import settings

logger = logging.getLogger(__name__)


# Normalize the URL so it survives copy/paste into host dashboards (Render etc.):
#   * strip stray whitespace and surrounding quotes
#   * upgrade the legacy "postgres://" scheme that SQLAlchemy 2.0 no longer accepts
def get_database_url() -> str:
    """Return the normalized DATABASE_URL (or the local SQLite fallback).

    Shared by the engine below and by Alembic's ``migrations/env.py`` so the
    app and migrations always target the same database + URL scheme:
      * strip stray whitespace / surrounding quotes (hosting dashboards add them)
      * upgrade the legacy ``postgres://`` scheme SQLAlchemy 2.0 rejects
      * fall back to a local SQLite file when DATABASE_URL is unset
    """
    raw = (settings.database_url or "").strip().strip('"').strip("'")
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    return raw or "sqlite:///./upsc_app.db"


_db_url = get_database_url()

# SQLite needs check_same_thread=False to work with FastAPI's threadpool.
_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}

# Hardened pool for free managed Postgres (Supabase/Neon): keep the pool
# small (few connection slots) and recycle idle connections before the
# provider drops them. SQLite keeps the simple single-connection path.
if _db_url.startswith("sqlite"):
    engine = create_engine(_db_url, pool_pre_ping=True, connect_args=_connect_args)
else:
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=280,
        pool_timeout=30,
        connect_args=_connect_args,
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


def get_db():
    """FastAPI dependency: yield a DB session and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Bring the schema to head via Alembic -- the single source of truth.

    Runs the same ``alembic upgrade head`` used in production (see render.yaml)
    but in-process, so local dev and the test suite converge on one mechanism.
    The Alembic env resolves the DB URL from ``get_database_url()``, so this
    always targets the app's configured database. ``upgrade`` is idempotent: a
    no-op at head, and it safely stamps a legacy pre-Alembic database on first
    run (the baseline migration is written to tolerate that).
    """
    from alembic import command
    from alembic.config import Config

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    # Keep the app's logging setup: env.py skips fileConfig when this is False.
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def init_db() -> None:
    """Ensure the database schema is at the latest Alembic revision."""
    run_migrations()
    safe = _db_url.split("@")[-1] if "@" in _db_url else _db_url
    logger.info(f"Database ready (Alembic head): {safe}")
