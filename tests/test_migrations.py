"""Alembic migration smoke test (offline, throwaway SQLite).

Guards against schema drift: verifies that ``alembic upgrade head`` builds every
table the SQLAlchemy ORM declares, and that ``downgrade base`` tears them down.
If a model is added/changed without a matching migration, this test (together
with ``alembic revision --autogenerate``) surfaces the gap.

Requires the ``alembic`` package (added to project deps in this step); the test
is skipped automatically if it is not installed.
"""

import ast
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

pytest.importorskip("alembic")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, event, inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402


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
            assert (
                conn.execute(text("SELECT active_session_id FROM users WHERE id='u1'")).scalar()
                is None
            )
            assert (
                conn.execute(text("SELECT session_id FROM refresh_tokens WHERE id='t1'")).scalar()
                is None
            )
        command.downgrade(cfg, "0003_jobs")
        assert "active_session_id" not in _columns(db_url, "users")
        assert "session_id" not in _columns(db_url, "refresh_tokens")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM refresh_tokens")).scalar() == 1
            assert (
                conn.execute(text("SELECT email FROM users WHERE id='u1'")).scalar()
                == "legacy@example.com"
            )
    finally:
        engine.dispose()


# --- PostgreSQL advisory lock (Phase 08 release hardening, ADR-018) ---------------
# ``migrations/env.py`` runs at import under Alembic, so it cannot be imported. The
# tests below load the lock helper straight from its source (AST) and check the
# call-site wiring structurally. Real lock behaviour (blocking, serialization of 3
# concurrent runners) needs PostgreSQL and is verified in the disposable staging
# databases, not here.

_LOCK_ID = 80080601


def _env_tree() -> ast.Module:
    return ast.parse((ROOT / "migrations" / "env.py").read_text(encoding="utf-8"))


def _lock_helper_namespace() -> dict:
    wanted = []
    for node in _env_tree().body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_MIGRATION_LOCK_ID" for t in node.targets
        ):
            wanted.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_acquire_migration_lock":
            wanted.append(node)
    assert len(wanted) == 2, "env.py must define _MIGRATION_LOCK_ID and _acquire_migration_lock"
    namespace = {"text": text}
    code = compile(ast.Module(body=wanted, type_ignores=[]), "migrations/env.py", "exec")
    exec(code, namespace)  # noqa: S102 - our own repository source, helper only
    return namespace


class _FakeConnection:
    def __init__(self, dialect_name: str):
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed: list[str] = []

    def execute(self, statement, *args, **kwargs):
        self.executed.append(str(statement))


def test_lock_id_is_the_approved_constant():
    assert _lock_helper_namespace()["_MIGRATION_LOCK_ID"] == _LOCK_ID


def test_postgresql_takes_one_transaction_scoped_advisory_lock():
    namespace = _lock_helper_namespace()
    conn = _FakeConnection("postgresql")
    namespace["_acquire_migration_lock"](conn)
    assert conn.executed == [f"SELECT pg_advisory_xact_lock({_LOCK_ID})"]
    # Transaction-scoped only: a session-level lock would not survive a
    # transaction-mode pooler and would outlive the migration transaction.
    assert "pg_advisory_lock" not in conn.executed[0]


@pytest.mark.parametrize("dialect_name", ["sqlite", "mysql", "mssql"])
def test_other_dialects_never_execute_the_lock(dialect_name):
    namespace = _lock_helper_namespace()
    conn = _FakeConnection(dialect_name)
    namespace["_acquire_migration_lock"](conn)
    assert conn.executed == []


def test_lock_is_taken_inside_the_migration_transaction_on_the_same_connection():
    fn = next(
        n
        for n in ast.walk(_env_tree())
        if isinstance(n, ast.FunctionDef) and n.name == "run_migrations_online"
    )
    withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
    outer = [w for w in withs if ast.unparse(w.items[0].context_expr) == "connectable.connect()"]
    inner = [
        w for w in withs if ast.unparse(w.items[0].context_expr) == "context.begin_transaction()"
    ]
    assert len(outer) == 1 and len(inner) == 1
    conn_name = outer[0].items[0].optional_vars.id
    # Lock first, migrations second, nothing in between.
    assert [ast.unparse(s) for s in inner[0].body] == [
        f"_acquire_migration_lock({conn_name})",
        "context.run_migrations()",
    ]
    # The transaction block lives inside the connection block ...
    assert inner[0] in list(ast.walk(outer[0]))
    # ... and Alembic is configured with that very connection.
    configure = [
        n
        for n in ast.walk(outer[0])
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "context.configure"
    ]
    assert len(configure) == 1
    assert {k.arg: ast.unparse(k.value) for k in configure[0].keywords}["connection"] == conn_name


def test_sqlite_upgrade_executes_no_advisory_lock_statement():
    db_path = pathlib.Path(tempfile.mkdtemp()) / "mig_nolock.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    seen: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _capture)
    try:
        command.upgrade(_make_config(db_url), "head")
    finally:
        event.remove(Engine, "before_cursor_execute", _capture)

    assert seen, "expected the migration run to execute statements"
    assert not [s for s in seen if "advisory" in s.lower()]
    assert {"study_plans", "evaluation_attempts"} <= _tables_in(db_url)


# --- Table security in 0005 / 0006 (Phase 08 release hardening, ADR-019) ------------
# The two backend-only tables get RLS (no policies) and have every privilege revoked
# from the Supabase API roles, PostgreSQL only. Real PostgreSQL behaviour (roles absent
# and present, owner access, anon/authenticated/service_role refused) is verified in the
# disposable staging databases; here the generated SQL is checked offline.


def _offline_postgresql_sql(start: str = "0004_single_active_session", end: str = "head") -> str:
    import io

    buffer = io.StringIO()
    cfg = Config(str(ROOT / "alembic.ini"), output_buffer=buffer)
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "postgresql://offline:offline@localhost/offline")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, f"{start}:{end}", sql=True)
    return buffer.getvalue()


@pytest.mark.parametrize("table", ["study_plans", "evaluation_attempts"])
def test_postgresql_migration_enables_rls_and_revokes_api_roles(table):
    sql = _offline_postgresql_sql()
    create_at = sql.index(f"CREATE TABLE {table}")
    enable = f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'
    assert enable in sql
    # Same transaction, right after the table exists.
    assert sql.index(enable) > create_at
    assert f"'{table}', api_role" in sql
    assert "ARRAY['anon', 'authenticated', 'service_role']" in sql
    assert "REVOKE ALL ON TABLE %I.%I FROM %I" in sql


def test_postgresql_migration_security_is_guarded_and_minimal():
    sql = _offline_postgresql_sql()
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
    # Roles that do not exist (plain PostgreSQL) are skipped, never an error.
    assert sql.count("SELECT 1 FROM pg_roles WHERE rolname = api_role") == 2
    upper = sql.upper()
    assert "FORCE ROW LEVEL SECURITY" not in upper
    assert "CREATE POLICY" not in upper
    assert "ALTER DEFAULT PRIVILEGES" not in upper
    assert "GRANT " not in upper.replace("REVOKE", "")


def test_postgresql_migration_security_touches_only_the_new_tables():
    sql = _offline_postgresql_sql()
    for existing in ("users", "refresh_tokens", "jobs", "conversations", "alembic_version"):
        assert f'ALTER TABLE "{existing}"' not in sql
        assert f"'{existing}', api_role" not in sql


def test_sqlite_upgrade_executes_no_rls_or_role_statements():
    db_path = pathlib.Path(tempfile.mkdtemp()) / "mig_nolockdown.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    seen: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _capture)
    try:
        command.upgrade(_make_config(db_url), "head")
    finally:
        event.remove(Engine, "before_cursor_execute", _capture)

    joined = "\n".join(seen).lower()
    assert "row level security" not in joined
    assert "pg_roles" not in joined
    assert "revoke all" not in joined
    assert {"study_plans", "evaluation_attempts"} <= _tables_in(db_url)
