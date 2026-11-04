"""Phase 08 Slice A (AC-A5): `GET /progress/overview`.

Covers authentication, the zeroed shape for a new user, per-user isolation of
Mentor messages and feedback, and the streak maths (current, longest, the
"yesterday" anchor and gaps). Rows with fixed timestamps are inserted directly
so the streak is deterministic; isolation goes through the real write routes.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.api.routes.progress import _ACTIVITY_DAYS, _streak
from src.core import models
from src.core.db import SessionLocal

URL = "/api/v1/progress/overview"

TOP_KEYS = {
    "streak",
    "topics",
    "weak_topics",
    "revision",
    "questions",
    "totals",
    "evaluations",
    "subjects",
    "weak_subjects",
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _add_messages(user_id: str, days_ago: list[int], role: str = "user") -> None:
    """One conversation; one message at noon UTC per entry in ``days_ago``."""
    today = _today()
    with SessionLocal() as db:
        convo = models.Conversation(user_id=user_id, agent="mentor", title="t")
        db.add(convo)
        db.flush()
        for n in days_ago:
            at = datetime.combine(today - timedelta(days=n), time(12), tzinfo=timezone.utc)
            db.add(models.Message(conversation_id=convo.id, role=role, content="q", created_at=at))
        db.commit()


def _overview(client, user) -> dict:
    r = client.get(URL, headers=user.headers)
    assert r.status_code == 200
    return r.json()


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_unauthenticated_is_401(client, headers):
    assert client.get(URL, headers=headers).status_code == 401


# --------------------------------------------------------------------------- #
# Empty state
# --------------------------------------------------------------------------- #
def test_new_user_gets_zeroed_shape(client, make_user):
    body = _overview(client, make_user("prog-empty"))
    assert set(body) == TOP_KEYS
    streak = body["streak"]
    assert (streak["current"], streak["longest"], streak["active_today"]) == (0, 0, False)
    assert len(streak["activity"]) == _ACTIVITY_DAYS
    assert all(day["count"] == 0 for day in streak["activity"])
    assert streak["activity"][-1]["date"] == _today().isoformat()
    assert body["topics"] == [] and body["weak_topics"] == []
    assert body["revision"] == {"due": 0, "total": 0, "subjects": []}
    assert body["questions"] == {"total": 0, "correct": 0, "accuracy": 0.0}
    assert body["totals"] == {"conversations": 0, "active_days": 0, "questions_asked": 0}


# --------------------------------------------------------------------------- #
# Ownership isolation
# --------------------------------------------------------------------------- #
def test_other_users_messages_and_feedback_are_invisible(client, make_user):
    u1, u2 = make_user("prog-u1"), make_user("prog-u2")
    saved = client.post(
        "/api/v1/history/messages",
        json={"role": "user", "content": "What is Article 21?", "agent": "mentor"},
        headers=u1.headers,
    )
    assert saved.status_code == 201
    reply = client.post(
        "/api/v1/history/messages",
        json={
            "role": "assistant",
            "content": "Right to life.",
            "agent": "mentor",
            "conversation_id": saved.json()["conversation_id"],
        },
        headers=u1.headers,
    )
    assert reply.status_code == 201
    for rating in ("up", "up", "down"):
        r = client.post("/api/v1/feedback/submit", json={"rating": rating}, headers=u1.headers)
        assert r.status_code == 201

    b1 = _overview(client, u1)
    assert b1["totals"] == {"conversations": 1, "active_days": 1, "questions_asked": 1}
    assert b1["questions"] == {"total": 3, "correct": 2, "accuracy": round(2 / 3, 4)}
    assert b1["streak"]["current"] == 1 and b1["streak"]["active_today"] is True

    b2 = _overview(client, u2)
    assert b2["totals"] == {"conversations": 0, "active_days": 0, "questions_asked": 0}
    assert b2["questions"] == {"total": 0, "correct": 0, "accuracy": 0.0}
    assert b2["streak"]["current"] == 0
    assert all(day["count"] == 0 for day in b2["streak"]["activity"])


def test_only_user_messages_count_as_activity(client, make_user):
    user = make_user("prog-roles")
    _add_messages(user.id, [0, 1], role="assistant")
    body = _overview(client, user)
    assert body["totals"]["questions_asked"] == 0
    assert body["totals"]["active_days"] == 0
    assert body["streak"]["current"] == 0


# --------------------------------------------------------------------------- #
# Streak maths through the API
# --------------------------------------------------------------------------- #
def test_streak_and_activity_from_real_rows(client, make_user):
    user = make_user("prog-streak")
    # Active today, 1, 2 days ago (current 3); a 4-day run 10-13 days ago (longest 4);
    # two questions on day 1; one 40 days ago (outside the 30-day strip).
    _add_messages(user.id, [0, 1, 1, 2, 10, 11, 12, 13, 40])
    body = _overview(client, user)
    streak = body["streak"]
    assert (streak["current"], streak["longest"], streak["active_today"]) == (3, 4, True)
    by_date = {d["date"]: d["count"] for d in streak["activity"]}
    today = _today()
    assert by_date[today.isoformat()] == 1
    assert by_date[(today - timedelta(days=1)).isoformat()] == 2
    assert by_date[(today - timedelta(days=3)).isoformat()] == 0
    assert (today - timedelta(days=40)).isoformat() not in by_date
    assert body["totals"] == {"conversations": 1, "active_days": 8, "questions_asked": 9}


def test_streak_anchors_on_yesterday_when_idle_today(client, make_user):
    user = make_user("prog-yday")
    _add_messages(user.id, [1, 2])
    streak = _overview(client, user)["streak"]
    assert (streak["current"], streak["longest"], streak["active_today"]) == (2, 2, False)


def test_gap_of_two_days_breaks_current_streak(client, make_user):
    user = make_user("prog-gap")
    _add_messages(user.id, [2, 3, 4])
    streak = _overview(client, user)["streak"]
    assert (streak["current"], streak["longest"], streak["active_today"]) == (0, 3, False)


# --------------------------------------------------------------------------- #
# Streak maths (pure helper, fixed "today")
# --------------------------------------------------------------------------- #
TODAY = date(2026, 11, 3)


def _days(*offsets: int) -> set[str]:
    return {(TODAY - timedelta(days=n)).isoformat() for n in offsets}


@pytest.mark.parametrize(
    "offsets, expected",
    [
        ((), (0, 0, False)),
        ((0,), (1, 1, True)),
        ((1,), (1, 1, False)),  # yesterday anchor keeps the streak alive
        ((2,), (0, 1, False)),  # two idle days end it
        ((0, 1, 2, 3), (4, 4, True)),
        ((1, 2, 3), (3, 3, False)),
        ((0, 2, 3, 4), (1, 3, True)),  # gap at day 1
        ((0, 1, 5, 6, 7, 8, 9), (2, 5, True)),  # longest run is in the past
        ((30, 31, 32), (0, 3, False)),
    ],
)
def test_streak_helper(offsets, expected):
    assert _streak(_days(*offsets), TODAY) == expected


def test_streak_helper_spans_month_and_year_boundaries():
    days = {"2026-12-30", "2026-12-31", "2027-01-01"}
    assert _streak(days, date(2027, 1, 1)) == (3, 3, True)
    assert _streak({"2026-02-28", "2026-03-01"}, date(2026, 3, 2)) == (2, 2, False)
