"""Dashboard progress route.

`GET /progress/overview` returns the snapshot the frontend Dashboard renders
(Mentor streak, Mentor activity strip, helpful-answer rate, totals).

Everything is derived live from the caller's own rows (conversations / messages /
feedback) - no extra tables required. The handler is fully fail-open: on any
error it returns a valid, empty-but-well-shaped payload so the dashboard always
renders instead of 500-ing.

Notes / honest limitations:
- Activity, streak, `active_days` and `questions_asked` count **Mentor chat only**:
  only the Mentor saves server-side history (user messages). The other workflows
  keep their state in the browser and contribute nothing here.
- `questions` is the thumbs-feedback (helpful-answer) rate, not answer accuracy:
  total = feedback rows, correct = 👍 ("up") count, accuracy = correct / total.
  The key names are kept for contract stability.
- Evaluation analytics (Phase 08 Slice C2, ADR-017) come from the caller's own
  `evaluation_attempts` rows (latest 500), computed on read by the pure functions
  in `src/core/evaluation_history.py`: `evaluations` (Average Evaluation Score,
  both modes, tagged and untagged), `subjects` (subject mastery), `weak_subjects`
  and `revision` (subject-based revision schedule, UTC days).
- `topics` / `weak_topics` are deprecated legacy keys and always `[]`; there is
  no topic-level data, only subject-level.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.deps import get_current_user
from src.core import evaluation_history, models
from src.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/progress", tags=["Progress"])

# How many trailing days the activity heat-strip covers.
_ACTIVITY_DAYS = 30


def _evaluation_analytics(db: Session, user_id: str, today: date) -> dict:
    """Slice C2 keys from the caller's own attempts (always filtered by ``user_id``).

    Fail-open on its own, so an analytics failure never hides the Mentor stats.
    """
    try:
        attempts = evaluation_history.load_attempts(db, user_id)
    except Exception as exc:
        logger.warning("progress_evaluation_analytics_failed (%s)", type(exc).__name__)
        db.rollback()  # leave no aborted transaction behind (PostgreSQL)
        return _evaluation_analytics_empty()
    return {
        "evaluations": evaluation_history.average_score(attempts),
        "subjects": evaluation_history.subject_mastery(attempts),
        "weak_subjects": evaluation_history.weak_subjects(attempts),
        "revision": evaluation_history.revision_schedule(attempts, today),
    }


def _empty_overview() -> dict:
    """A valid, zeroed ProgressOverview (used as the fail-open fallback)."""
    today = datetime.now(timezone.utc).date()
    activity = [
        {"date": (today - timedelta(days=i)).isoformat(), "count": 0}
        for i in range(_ACTIVITY_DAYS - 1, -1, -1)
    ]
    return {
        "streak": {
            "current": 0,
            "longest": 0,
            "active_today": False,
            "activity": activity,
        },
        "topics": [],
        "weak_topics": [],
        "questions": {"total": 0, "correct": 0, "accuracy": 0.0},
        "totals": {"conversations": 0, "active_days": 0, "questions_asked": 0},
        **_evaluation_analytics_empty(),
    }


def _evaluation_analytics_empty() -> dict:
    """The zeroed Slice C2 keys (no attempts)."""
    return {
        "evaluations": evaluation_history.average_score([]),
        "subjects": [],
        "weak_subjects": [],
        "revision": {"due": 0, "total": 0, "subjects": []},
    }


def _as_day(value) -> str | None:
    """Normalise a DB `date(created_at)` result to a 'YYYY-MM-DD' string.

    SQLite returns a str, Postgres returns a date/datetime - handle both.
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _streak(active_days: set[str], today: date) -> tuple[int, int, bool]:
    """Return (current_streak, longest_streak, active_today) from a day set."""
    if not active_days:
        return 0, 0, False

    parsed = sorted({datetime.strptime(d, "%Y-%m-%d").date() for d in active_days if d})

    # Longest run of consecutive calendar days.
    longest = 1
    run = 1
    for prev, cur in zip(parsed, parsed[1:]):
        if (cur - prev).days == 1:
            run += 1
        else:
            run = 1
        longest = max(longest, run)

    # Current streak: count back from today (or yesterday, so one idle day at
    # the very start of "today" doesn't reset an active streak).
    day_set = set(parsed)
    active_today = today in day_set
    anchor = today if active_today else today - timedelta(days=1)
    current = 0
    cursor = anchor
    while cursor in day_set:
        current += 1
        cursor -= timedelta(days=1)

    return current, longest, active_today


@router.get("/overview")
def progress_overview(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Live study-progress snapshot for the authenticated user."""
    try:
        uid = user["id"]
        today = datetime.now(timezone.utc).date()

        # --- Totals -------------------------------------------------------
        conversations = (
            db.query(func.count(models.Conversation.id))
            .filter(models.Conversation.user_id == uid)
            .scalar()
            or 0
        )

        questions_asked = (
            db.query(func.count(models.Message.id))
            .join(
                models.Conversation,
                models.Message.conversation_id == models.Conversation.id,
            )
            .filter(
                models.Conversation.user_id == uid,
                models.Message.role == "user",
            )
            .scalar()
            or 0
        )

        # --- Per-day activity (user messages) -----------------------------
        day_col = func.date(models.Message.created_at)
        rows = (
            db.query(day_col, func.count(models.Message.id))
            .join(
                models.Conversation,
                models.Message.conversation_id == models.Conversation.id,
            )
            .filter(
                models.Conversation.user_id == uid,
                models.Message.role == "user",
            )
            .group_by(day_col)
            .all()
        )
        counts_by_day: dict[str, int] = {}
        for day_value, cnt in rows:
            key = _as_day(day_value)
            if key:
                counts_by_day[key] = counts_by_day.get(key, 0) + int(cnt or 0)

        active_days_total = len(counts_by_day)

        activity = [
            {
                "date": (today - timedelta(days=i)).isoformat(),
                "count": counts_by_day.get((today - timedelta(days=i)).isoformat(), 0),
            }
            for i in range(_ACTIVITY_DAYS - 1, -1, -1)
        ]

        current, longest, active_today = _streak(set(counts_by_day), today)

        # --- Questions (thumbs feedback as the available signal) ----------
        fb_total = (
            db.query(func.count(models.Feedback.id)).filter(models.Feedback.user_id == uid).scalar()
            or 0
        )
        fb_up = (
            db.query(func.count(models.Feedback.id))
            .filter(
                models.Feedback.user_id == uid,
                models.Feedback.rating == "up",
            )
            .scalar()
            or 0
        )
        accuracy = round(fb_up / fb_total, 4) if fb_total else 0.0

        return {
            "streak": {
                "current": current,
                "longest": longest,
                "active_today": active_today,
                "activity": activity,
            },
            "topics": [],
            "weak_topics": [],
            "questions": {
                "total": int(fb_total),
                "correct": int(fb_up),
                "accuracy": accuracy,
            },
            "totals": {
                "conversations": int(conversations),
                "active_days": int(active_days_total),
                "questions_asked": int(questions_asked),
            },
            **_evaluation_analytics(db, uid, today),
        }
    except Exception:  # pragma: no cover - fail-open so the dashboard renders
        logger.warning("progress_overview failed; returning empty snapshot", exc_info=True)
        return _empty_overview()
