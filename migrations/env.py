"""Alembic runtime environment for UPSC AI Pro.

Resolves the database URL from the application settings (the same normalized
DATABASE_URL the app uses) so Alembic and the app never diverge. A URL passed
via ``config.set_main_option("sqlalchemy.url", ...)`` (e.g. from tests) takes
precedence, which lets the migration test target a throwaway SQLite file.
"""
from __future__ import annotations

import pathlib
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project importable when Alembic runs from the repo root.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.db import Base, get_database_url  # noqa: E402
from src.core import models  # noqa: E402,F401  (registers all tables on Base.metadata)

config = context.config

# URL resolution: an explicitly configured URL (tests / CLI -x) wins; otherwise
# fall back to the app's normalized DATABASE_URL.
_cfg_url = config.get_main_option("sqlalchemy.url")
url = _cfg_url or get_database_url()
# ConfigParser (used under the hood by set_main_option) treats "%" as
# interpolation syntax, so a URL-encoded password like "%40" ("@") would raise
# "invalid interpolation syntax". Escape "%" -> "%%" for the stored copy; the
# raw ``url`` variable below is what we actually connect with.
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

# CLI runs configure logging from alembic.ini. In-process callers (the app's
# init_db) set configure_logger=False: fileConfig would otherwise disable every
# existing app logger and replace the app's root handler.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_is_sqlite = url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=_is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=_is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
