"""Alembic migration smoke test (offline, throwaway SQLite).

Guards against schema drift: verifies that ``alembic upgrade head`` builds every
table the SQLAlchemy ORM declares, and that ``downgrade base`` tears them down.
If a model is added/changed without a matching migration, this test (together
with ``alembic revision --autogenerate``) surfaces the gap.

Requires the ``alembic`` package (added to project deps in this step); the test
is skipped automatically if it is not installed.
"""

import pathlib
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

pytest.importorskip("alembic")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402


def _make_config(db_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Schema tests only: skip env.py's fileConfig so the shared pytest process
    # keeps its loggers (CLI logging is covered in test_logging_startup.py).
    cfg.attributes["configure_logger"] = False
    return cfg


def _expected_tables() -> set[str]:
    from src.core import models  # noqa: F401  (registers tables)
    from src.core.db import Base

    return set(Base.metadata.tables.keys())


def _tables_in(db_url: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_head_creates_all_orm_tables():
    db_path = pathlib.Path(tempfile.mkdtemp()) / "mig_up.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    command.upgrade(_make_config(db_url), "head")

    tables = _tables_in(db_url)
    missing = _expected_tables() - tables
    assert not missing, f"migration is missing ORM tables: {sorted(missing)}"


def test_downgrade_base_drops_orm_tables():
    db_path = pathlib.Path(tempfile.mkdtemp()) / "mig_down.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    cfg = _make_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    leftover = _expected_tables() & _tables_in(db_url)
    assert not leftover, f"downgrade left ORM tables behind: {sorted(leftover)}"


def _columns(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_head_has_single_session_columns():
    db_url = f"sqlite:///{(pathlib.Path(tempfile.mkdtemp()) / 'mig_0004.db').as_posix()}"
    command.upgrade(_make_config(db_url), "head")
    assert "active_session_id" in _columns(db_url, "users")
    assert "session_id" in _columns(db_url, "refresh_tokens")


def test_head_column_nullability_matches_orm():
    """Every ORM column exists after ``upgrade head`` with the same nullability.

    Primary keys are skipped: SQLite reports non-integer primary keys as nullable.
    """
    from src.core import models  # noqa: F401  (registers tables)
    from src.core.db import Base

    db_url = f"sqlite:///{(pathlib.Path(tempfile.mkdtemp()) / 'mig_nullable.db').as_posix()}"
    command.upgrade(_make_config(db_url), "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        drift = []
        for name, table in Base.metadata.tables.items():
            db_cols = {c["name"]: c for c in insp.get_columns(name)}
            for col in table.columns:
                if col.primary_key:
                    continue
                if col.name not in db_cols:
                    drift.append(f"{name}.{col.name}: missing in migrations")
                elif bool(db_cols[col.name]["nullable"]) != bool(col.nullable):
                    drift.append(
                        f"{name}.{col.name}: db nullable={db_cols[col.name]['nullable']}, "
                        f"orm nullable={col.nullable}"
                    )
    finally:
        engine.dispose()
    assert not drift, "schema drift between migrations and ORM: " + "; ".join(drift)


def test_upgrade_from_pre_0004_preserves_data_and_downgrades():
    """Synthetic pre-0004 database: existing rows survive; legacy rows get NULLs."""
    from sqlalchemy import text

    db_url = f"sqlite:///{(pathlib.Path(tempfile.mkdtemp()) / 'mig_pre0004.db').as_posix()}"
    cfg = _make_config(db_url)
    command.upgrade(cfg, "0003_jobs")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, hashed_password, email_verified, created_at) "
                    "VALUES ('u1', 'legacy@example.com', 'L', 'x', 1, '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at) "
                    "VALUES ('t1', 'u1', 'h1', '2099-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT active_session_id FROM users WHERE id='u1'")).scalar() is None
            assert conn.execute(text("SELECT session_id FROM refresh_tokens WHERE id='t1'")).scalar() is None
        command.downgrade(cfg, "0003_jobs")
        assert "active_session_id" not in _columns(db_url, "users")
        assert "session_id" not in _columns(db_url, "refresh_tokens")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM refresh_tokens")).scalar() == 1
            assert conn.execute(text("SELECT email FROM users WHERE id='u1'")).scalar() == "legacy@example.com"
    finally:
        engine.dispose()
