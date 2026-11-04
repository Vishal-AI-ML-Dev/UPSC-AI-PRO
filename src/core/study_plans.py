"""Latest-study-plan persistence (Phase 08 Slice B, ADR-017).

Every function is keyed by the authenticated ``user_id``; no caller can address
a row by its id alone. ``upsert_plan`` is one atomic statement
(``INSERT … ON CONFLICT (user_id) DO UPDATE … RETURNING``) built with the
session dialect's ``insert`` — no read-before-write — so concurrent saves for one
user never create a second row and never mix two payloads.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.models import StudyPlan

logger = logging.getLogger(__name__)

# How many times the upsert statement is attempted (the first try + one retry).
UPSERT_ATTEMPTS = 2


class PlanStoreUnavailable(Exception):
    """The plan could not be saved (unsupported dialect or a persistent DB error)."""


class PlanNotFound(Exception):
    """The caller has no saved plan."""


class PlanReplaced(Exception):
    """The caller has a plan, but not the one the request referred to."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def iso(value: datetime | None) -> str:
    """ISO-8601 in UTC. SQLite returns naive datetimes; they are stored as UTC."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _dialect_insert(db: Session):
    """The ``insert`` construct with ``on_conflict_do_update`` for this dialect."""
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise PlanStoreUnavailable(f"unsupported dialect: {name}")
    return insert


def to_dict(row: StudyPlan) -> dict:
    progress = _loads(row.progress, {})
    return {
        "id": row.id,
        "goal": row.goal,
        "inputs": _loads(row.inputs, {}),
        "plan_md": row.plan_md,
        "meta": _loads(row.meta, {}) or None,
        "progress": {k: v for k, v in progress.items() if isinstance(v, bool)},
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def get_plan(db: Session, user_id: str) -> dict | None:
    row = db.scalars(select(StudyPlan).where(StudyPlan.user_id == user_id)).first()
    return to_dict(row) if row else None


def upsert_statement(insert, values: dict):
    """``INSERT … ON CONFLICT (user_id) DO UPDATE … RETURNING id, updated_at``.

    ``created_at`` and ``user_id`` keep the row's first values; every other
    column comes from this request's payload, and progress is reset.
    """
    stmt = insert(StudyPlan).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=[StudyPlan.user_id],
        set_={
            "id": stmt.excluded.id,
            "goal": stmt.excluded.goal,
            "inputs": stmt.excluded.inputs,
            "plan_md": stmt.excluded.plan_md,
            "meta": stmt.excluded.meta,
            "progress": "{}",
            "updated_at": stmt.excluded.updated_at,
        },
    ).returning(StudyPlan.id, StudyPlan.updated_at)


def _upsert_once(
    db: Session, user_id: str, goal: str, inputs: dict, plan_md: str, meta: dict | None
) -> tuple[str, datetime]:
    now = _now()
    values = {
        "id": _new_id(),
        "user_id": user_id,
        "goal": goal,
        "inputs": json.dumps(inputs),
        "plan_md": plan_md,
        "meta": json.dumps(meta) if meta is not None else None,
        "progress": "{}",
        "created_at": now,
        "updated_at": now,
    }
    row = db.execute(upsert_statement(_dialect_insert(db), values)).one()
    db.commit()
    return row[0], row[1]


def upsert_plan(
    db: Session, user_id: str, goal: str, inputs: dict, plan_md: str, meta: dict | None
) -> dict:
    """Save the caller's plan, replacing any previous one (new id, progress reset).

    One statement per attempt. A residual ``IntegrityError`` / ``OperationalError``
    (e.g. SQLite ``database is locked``) is rolled back and retried once; if it
    still fails, ``PlanStoreUnavailable`` is raised (the route answers 503).
    """
    for attempt in range(1, UPSERT_ATTEMPTS + 1):
        try:
            plan_id, updated_at = _upsert_once(db, user_id, goal, inputs, plan_md, meta)
            return {"id": plan_id, "updated_at": iso(updated_at)}
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            logger.warning(
                "study_plan_upsert_failed",
                extra={"attempt": attempt, "error_type": type(exc).__name__},
            )
        except SQLAlchemyError as exc:
            # Not a conflict/lock: no retry. Handled here so the global handler never
            # logs the statement parameters (plan text).
            db.rollback()
            logger.warning(
                "study_plan_upsert_failed",
                extra={"attempt": attempt, "error_type": type(exc).__name__},
            )
            break
    raise PlanStoreUnavailable("study plan upsert failed")


def update_progress(db: Session, user_id: str, plan_id: str, progress: dict) -> dict:
    """Replace the progress map of the caller's current plan.

    Applied only when ``plan_id`` is the caller's current plan: otherwise
    ``PlanReplaced`` if the caller has a (different) plan, else ``PlanNotFound``.
    """
    stmt = (
        update(StudyPlan)
        .where(StudyPlan.user_id == user_id, StudyPlan.id == plan_id)
        .values(progress=json.dumps(progress), updated_at=_now())
        .returning(StudyPlan.id, StudyPlan.progress, StudyPlan.updated_at)
    )
    row = db.execute(stmt).first()
    db.commit()
    if row is None:
        current = db.scalar(select(StudyPlan.id).where(StudyPlan.user_id == user_id))
        if current is None:
            raise PlanNotFound()
        raise PlanReplaced()
    return {"id": row[0], "progress": _loads(row[1], {}), "updated_at": iso(row[2])}


def delete_plan(db: Session, user_id: str) -> None:
    """Delete the caller's plan, if any (idempotent)."""
    db.execute(delete(StudyPlan).where(StudyPlan.user_id == user_id))
    db.commit()
