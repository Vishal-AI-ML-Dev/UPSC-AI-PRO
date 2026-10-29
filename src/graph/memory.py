"""Persistent memory wiring: LangGraph checkpointer + long-term store.

Two layers of memory, both backed by the SAME database the app already uses:

* Checkpointer (short-term): per-thread conversation state, so a graph run can
  resume and remember the current conversation. Postgres in production, SQLite
  for local development, in-memory as a last resort.
* Store (long-term): cross-thread memory such as the student's profile and weak
  areas, addressable by ``user_id`` regardless of thread.

This module ONLY reuses ``settings.database_url``; it never touches the existing
SQLAlchemy auth engine, the ``users`` table, or any auth logic. Both factories
are process-wide singletons created lazily on first use.

Production note: the connections use ``autocommit=True`` and
``prepare_threshold=0`` so they work safely behind connection poolers such as
Supabase / pgBouncer (which do not support server-side prepared statements).
Call ``close_memory()`` on application shutdown to release the pools cleanly.
"""

from __future__ import annotations

import logging

from src.core.config import settings

logger = logging.getLogger(__name__)

# Connection kwargs required for poolers (Supabase / pgBouncer).
_PG_KWARGS = {"autocommit": True, "prepare_threshold": 0}

_checkpointer = None
_store = None
# Connection pools we own, tracked so they can be closed on shutdown.
_pools: list = []


def _is_postgres(url: str) -> bool:
    return url.startswith("postgres://") or url.startswith("postgresql://")


# Single shared connection pool for BOTH the checkpointer and the store.
# Managed poolers (e.g. Supabase free tier / pgBouncer) allow only a handful of
# concurrent connections. Opening one pool per layer (20 + 10) exhausts that
# budget and the second layer times out. A single small pool keeps total usage
# low so both layers stay durable on Postgres.
_pg_pool = None


def _get_shared_pg_pool(url: str):
    """Return a process-wide psycopg connection pool, created lazily.

    Shared by the checkpointer and the store. Conservatively sized so the app
    stays well under the connection ceiling of managed poolers.
    """
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=5,
        timeout=10,
        kwargs=_PG_KWARGS,
        open=False,
    )
    pool.open(wait=True, timeout=15)
    _pools.append(pool)
    _pg_pool = pool
    return _pg_pool


# ============================ Checkpointer (short-term) ========================
def get_checkpointer():
    """Return a process-wide checkpointer, creating it on first use.

    Resolution order: Postgres (if DATABASE_URL is Postgres) -> SQLite (local) ->
    in-memory (last resort). Falls back gracefully if an optional dependency or
    the database is unavailable, so the app never fails to start.
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    url = settings.database_url or ""

    if _is_postgres(url):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            pool = _get_shared_pg_pool(url)
            checkpointer = PostgresSaver(pool)
            checkpointer.setup()  # creates checkpoint tables if missing
            logger.info("Checkpointer: Postgres (shared DATABASE_URL)")
            _checkpointer = checkpointer
            return _checkpointer
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("Postgres checkpointer unavailable (%s); falling back", exc)

    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()
        logger.info("Checkpointer: SQLite (local dev -> checkpoints.sqlite)")
        _checkpointer = checkpointer
        return _checkpointer
    except Exception as exc:  # pragma: no cover
        logger.warning("SQLite checkpointer unavailable (%s); using in-memory", exc)

    from langgraph.checkpoint.memory import InMemorySaver

    logger.info("Checkpointer: in-memory (NOT persistent)")
    _checkpointer = InMemorySaver()
    return _checkpointer


# ============================ Store (long-term) ================================
def get_store():
    """Return a process-wide long-term store, creating it on first use."""
    global _store
    if _store is not None:
        return _store

    url = settings.database_url or ""

    if _is_postgres(url):
        try:
            from langgraph.store.postgres import PostgresStore

            pool = _get_shared_pg_pool(url)
            store = PostgresStore(pool)
            store.setup()  # creates store tables if missing
            logger.info("Store: Postgres (shared DATABASE_URL)")
            _store = store
            return _store
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("Postgres store unavailable (%s); using in-memory", exc)

    try:
        from langgraph.store.memory import InMemoryStore

        logger.info("Store: in-memory (NOT persistent)")
        _store = InMemoryStore()
        return _store
    except Exception as exc:  # pragma: no cover - depends on env
        logger.warning("In-memory store unavailable (%s); running without long-term store", exc)
        _store = None
        return _store


def close_memory() -> None:
    """Close all owned connection pools. Call on application shutdown.

    Safe to call multiple times; resets the cached singletons so the next
    ``get_*`` call rebuilds them.
    """
    global _checkpointer, _store
    for pool in _pools:
        try:
            pool.close()
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to close connection pool (%s)", exc)
    _pools.clear()
    _checkpointer = None
    _store = None


# ============================ Long-term profile helpers ========================
# Namespace convention: ("students", <user_id>) with a fixed "profile" key.
_PROFILE_KEY = "profile"


def _profile_namespace(user_id: str) -> tuple[str, str]:
    return ("students", user_id)


def save_student_profile(store, user_id: str, profile: dict) -> None:
    """Persist (upsert) a student's long-term profile."""
    store.put(_profile_namespace(user_id), _PROFILE_KEY, profile)


def load_student_profile(store, user_id: str) -> dict:
    """Load a student's long-term profile, or an empty dict if none exists."""
    item = store.get(_profile_namespace(user_id), _PROFILE_KEY)
    return item.value if item else {}


# ============================ Local smoke test =================================
if __name__ == "__main__":
    try:
        checkpointer = get_checkpointer()
        store = get_store()
        print("Checkpointer:", type(checkpointer).__name__)
        print("Store:", type(store).__name__)

        # Long-term profile round-trip.
        save_student_profile(
            store,
            "test-user",
            {"weak_areas": ["Economy", "CSAT"], "stage": "Prelims", "attempt": 2},
        )
        print("Loaded profile:", load_student_profile(store, "test-user"))
    finally:
        # Release pools so the process exits cleanly (no pool-worker warnings).
        close_memory()
