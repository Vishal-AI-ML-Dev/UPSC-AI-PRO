"""Evaluation analytics (Phase 08 Slice C2, ADR-017; AC-C2-1 .. AC-C2-3).

Pure functions in ``src/core/evaluation_history.py`` are tested with a fixed
``TODAY`` and fixed timestamps; the ``/progress/overview`` API is tested through
the real app with rows inserted for two users (isolation), using fixed dates
far enough in the past that the revision "due" flags never depend on the clock.

Fully offline: the only model call (end-to-end test) is a RunnableLambda stand-in.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.evaluator.graph as evaluator
import src.api.routes.progress as progress_routes
import src.core.evaluation_history as eh
from src.core.db import SessionLocal
from src.core.evaluation_history import Attempt
from src.core.models import EvaluationAttempt

URL = "/api/v1/progress/overview"
TODAY = date(2026, 11, 4)
NOON = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def att(
    score: float | None,
    max_score: int = 10,
    subject: str | None = "Economy",
    mode: str = "basic",
    days_ago: int = 0,
    seq: int = 0,
) -> Attempt:
    """One attempt at noon UTC ``days_ago`` days before TODAY (+``seq`` minutes for order)."""
    at = NOON - timedelta(days=days_ago) + timedelta(minutes=seq)
    return Attempt(mode, subject, score, max_score, at)


def chronological(scores: list[float | None], subject: str = "Economy", **kw) -> list[Attempt]:
    """Attempts in time order, oldest first, the last one at TODAY noon."""
    n = len(scores)
    return [att(s, subject=subject, seq=i - n + 1, **kw) for i, s in enumerate(scores)]


# --------------------------------------------------------------------------- #
# Constants (R-C2-1)
# --------------------------------------------------------------------------- #
def test_constants_match_the_spec():
    assert eh.PASS_PCT == 0.60
    assert eh.WEAK_MIN_ATTEMPTS == 3
    assert eh.MASTERY_WINDOW == 10
    assert eh.REVISION_INTERVALS_DAYS == (1, 3, 7, 14)
    assert eh.ANALYTICS_ATTEMPT_LIMIT == 500


# --------------------------------------------------------------------------- #
# Average Evaluation Score (R-C2-2)
# --------------------------------------------------------------------------- #
def test_average_includes_untagged_and_both_modes_excludes_unscored():
    attempts = [
        att(6, 10, subject="Economy", mode="basic"),  # 0.60 tagged
        att(9, 15, subject=None, mode="mains"),  # 0.60 untagged
        att(2, 10, subject=None, mode="basic"),  # 0.20 untagged
        att(None, 10, subject="Economy", mode="basic"),  # unscored: counted, not averaged
    ]
    summary = eh.average_score(attempts)
    assert summary["attempts"] == 4
    assert summary["scored"] == 3
    assert summary["average_score_pct"] == round((0.6 + 0.6 + 0.2) / 3, 4)
    assert summary["by_mode"] == {
        "basic": {"attempts": 3, "scored": 2, "average_score_pct": 0.4},
        "mains": {"attempts": 1, "scored": 1, "average_score_pct": 0.6},
    }


def test_average_with_no_scored_attempts_is_zeroed():
    zero = {"attempts": 0, "scored": 0, "average_score_pct": 0.0}
    assert eh.average_score([]) == {**zero, "by_mode": {"basic": zero, "mains": zero}}
    only_unscored = eh.average_score([att(None)])
    assert (only_unscored["attempts"], only_unscored["scored"]) == (1, 0)
    assert only_unscored["average_score_pct"] == 0.0


@pytest.mark.parametrize(
    "score, max_score",
    [(-1, 10), (11, 10), (float("nan"), 10), (float("inf"), 10), (5, 0), (True, 10)],
    ids=["negative", "above-max", "nan", "inf", "zero-max", "bool"],
)
def test_invalid_rows_are_never_scored(score, max_score):
    assert eh.attempt_pct(att(score, max_score)) is None
    assert eh.average_score([att(score, max_score)])["scored"] == 0
    assert eh.subject_mastery([att(score, max_score)]) == []


# --------------------------------------------------------------------------- #
# Subject mastery (R-C2-3)
# --------------------------------------------------------------------------- #
def test_mastery_uses_the_latest_ten_scored_attempts():
    # Oldest two fail at 0 %; the latest ten are perfect -> mastery 1.0 over 10.
    attempts = chronological([0, 0] + [10] * 10)
    assert eh.subject_mastery(attempts) == [{"subject": "Economy", "mastery": 1.0, "attempts": 10}]


def test_mastery_window_skips_unscored_attempts():
    # 10 scored perfect answers interleaved with unscored ones, then an old fail.
    scores: list[float | None] = [0]
    for _ in range(10):
        scores += [None, 10]
    assert eh.subject_mastery(chronological(scores)) == [
        {"subject": "Economy", "mastery": 1.0, "attempts": 10}
    ]


def test_untagged_attempts_are_in_no_subject_list():
    attempts = [att(1, subject=None, seq=i) for i in range(5)] + [att(8, subject="Ethics")]
    assert [r["subject"] for r in eh.subject_mastery(attempts)] == ["Ethics"]
    assert eh.weak_subjects(attempts) == []
    assert [s["subject"] for s in eh.revision_schedule(attempts, TODAY)["subjects"]] == ["Ethics"]


# --------------------------------------------------------------------------- #
# Weak subjects (R-C2-4)
# --------------------------------------------------------------------------- #
def test_two_low_attempts_are_not_weak_three_are():
    assert eh.weak_subjects(chronological([1, 1])) == []
    assert eh.weak_subjects(chronological([1, 1, 1])) == [
        {"subject": "Economy", "mastery": 0.1, "attempts": 3}
    ]


@pytest.mark.parametrize(
    "scores, max_score",
    [([6, 6, 6], 10), ([5, 7, 6], 10), ([9, 9, 9], 15), ([4, 8, 6], 10)],
    ids=["6-of-10", "mean-0.6", "9-of-15", "mean-0.6-float"],
)
def test_mastery_exactly_sixty_percent_is_not_weak(scores, max_score):
    attempts = chronological(scores, max_score=max_score)
    assert eh.subject_mastery(attempts)[0]["mastery"] == 0.6
    assert eh.weak_subjects(attempts) == []


def test_just_below_sixty_percent_is_weak():
    attempts = chronological([6, 6, 5.9])
    assert [r["subject"] for r in eh.weak_subjects(attempts)] == ["Economy"]


def test_weak_threshold_uses_the_window_not_old_history():
    # Old failures outside the window do not make a subject weak.
    attempts = chronological([0] * 5 + [8] * 10)
    assert eh.weak_subjects(attempts) == []


def test_sort_is_deterministic_with_ties():
    attempts = (
        chronological([2, 2, 2], subject="Geography")
        + chronological([1, 1, 1], subject="Ethics")
        + chronological([2, 2, 2], subject="Economy")  # ties Geography at 0.2
        + chronological([9, 9, 9], subject="Polity & Governance")
    )
    expected_weak = [
        {"subject": "Ethics", "mastery": 0.1, "attempts": 3},
        {"subject": "Economy", "mastery": 0.2, "attempts": 3},
        {"subject": "Geography", "mastery": 0.2, "attempts": 3},
    ]
    rng = random.Random(8)
    for _ in range(10):
        shuffled = attempts[:]
        rng.shuffle(shuffled)
        assert eh.weak_subjects(shuffled) == expected_weak
        assert [r["subject"] for r in eh.subject_mastery(shuffled)] == [
            "Ethics",
            "Economy",
            "Geography",
            "Polity & Governance",
        ]


# --------------------------------------------------------------------------- #
# Subject-based revision (R-C2-5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "k, interval", [(0, 1), (1, 1), (2, 3), (3, 7), (4, 14), (5, 14), (50, 14)]
)
def test_interval_table(k, interval):
    assert eh.revision_interval_days(k) == interval


@pytest.mark.parametrize("passes, interval", [(1, 1), (2, 3), (3, 7), (4, 14), (5, 14)])
def test_pass_runs_extend_the_interval(passes, interval):
    schedule = eh.revision_schedule(chronological([8] * passes), TODAY)
    row = schedule["subjects"][0]
    assert row["next_due"] == (TODAY + timedelta(days=interval)).isoformat()
    assert row["due"] is False


@pytest.mark.parametrize(
    "scores, interval",
    [
        ([8, 8, 3], 1),
        ([3, 8], 1),
        ([3], 1),
        ([8, 8, 3, 8, 8], 3),
        ([6, 6, 6], 7),
        ([8, None, 8], 3),
    ],
    ids=[
        "pass-pass-fail",
        "fail-then-pass",
        "single-fail",
        "reset-then-two",
        "exact-60",
        "unscored-between",
    ],
)
def test_fail_resets_the_run(scores, interval):
    row = eh.revision_schedule(chronological(scores), TODAY)["subjects"][0]
    assert row["next_due"] == (TODAY + timedelta(days=interval)).isoformat()


def test_unscored_attempts_are_ignored_and_do_not_reanchor():
    # Two passes three days ago, then an unscored attempt today.
    attempts = [att(8, days_ago=3, seq=0), att(8, days_ago=3, seq=1), att(None, days_ago=0)]
    schedule = eh.revision_schedule(attempts, TODAY)
    assert schedule["subjects"] == [
        {"subject": "Economy", "next_due": TODAY.isoformat(), "due": True}  # 3 days ago + 3
    ]
    assert eh.revision_schedule([att(None)], TODAY) == {"due": 0, "total": 0, "subjects": []}


def test_next_due_today_is_due_tomorrow_is_not():
    due_today = eh.revision_schedule([att(3, days_ago=1)], TODAY)["subjects"][0]
    assert due_today == {"subject": "Economy", "next_due": TODAY.isoformat(), "due": True}
    not_yet = eh.revision_schedule([att(3, days_ago=0)], TODAY)["subjects"][0]
    assert not_yet["due"] is False
    assert not_yet["next_due"] == (TODAY + timedelta(days=1)).isoformat()


def test_each_scored_attempt_reanchors():
    # Four passes long ago (k=4), then a pass today -> anchored today, k=5 -> 14 days.
    attempts = [att(8, days_ago=40 + i) for i in range(4)] + [att(8, days_ago=0)]
    row = eh.revision_schedule(attempts, TODAY)["subjects"][0]
    assert row["next_due"] == (TODAY + timedelta(days=14)).isoformat()


def test_anchor_is_the_utc_date():
    ist = timezone(timedelta(hours=5, minutes=30))
    # 01:00 IST on Nov 4 is 19:30 UTC on Nov 3.
    aware = Attempt("basic", "Economy", 3, 10, datetime(2026, 11, 4, 1, tzinfo=ist))
    assert eh.revision_schedule([aware], TODAY)["subjects"][0]["next_due"] == "2026-11-04"
    naive = Attempt("basic", "Economy", 3, 10, datetime(2026, 11, 3, 23, 59))  # SQLite: UTC
    assert eh.revision_schedule([naive], TODAY)["subjects"][0]["next_due"] == "2026-11-04"


def test_schedule_counts_and_order():
    attempts = [
        att(3, subject="Geography", days_ago=5),  # due 5 days ago + 1
        att(3, subject="Ethics", days_ago=5),  # same next_due -> name order
        att(8, subject="Economy", days_ago=0),  # due tomorrow
    ]
    schedule = eh.revision_schedule(attempts, TODAY)
    assert schedule["due"] == 2 and schedule["total"] == 3
    assert [s["subject"] for s in schedule["subjects"]] == ["Ethics", "Geography", "Economy"]


# --------------------------------------------------------------------------- #
# API (AC-C2-3)
# --------------------------------------------------------------------------- #
OLD = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def _insert(user_id: str, rows: list[tuple[str, str | None, float | None, int]]) -> None:
    """Insert (mode, subject, score, max_score) rows one minute apart from OLD."""
    with SessionLocal() as db:
        for i, (mode, subject, score, max_score) in enumerate(rows):
            db.add(
                EvaluationAttempt(
                    user_id=user_id,
                    mode=mode,
                    subject=subject,
                    score=score,
                    max_score=max_score,
                    created_at=OLD + timedelta(minutes=i),
                )
            )
        db.commit()


def _overview(client, user) -> dict:
    r = client.get(URL, headers=user.headers)
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_overview_requires_auth(client, headers):
    assert client.get(URL, headers=headers).status_code == 401


def test_empty_user_gets_zeroed_analytics_and_legacy_keys(client, make_user):
    body = _overview(client, make_user("c2-empty"))
    zero = {"attempts": 0, "scored": 0, "average_score_pct": 0.0}
    assert body["evaluations"] == {**zero, "by_mode": {"basic": zero, "mains": zero}}
    assert body["subjects"] == [] and body["weak_subjects"] == []
    assert body["revision"] == {"due": 0, "total": 0, "subjects": []}
    assert body["topics"] == [] and body["weak_topics"] == []
    assert body["questions"] == {"total": 0, "correct": 0, "accuracy": 0.0}


def test_two_users_are_isolated(client, make_user):
    u1, u2 = make_user("c2-u1"), make_user("c2-u2")
    _insert(
        u1.id,
        [
            ("basic", "Economy", 2, 10),
            ("basic", "Economy", 3, 10),
            ("mains", "Economy", 3, 15),
            ("basic", None, 9, 10),  # untagged: average only
            ("basic", "Economy", None, 10),  # unscored
        ],
    )
    _insert(u2.id, [("mains", "Polity & Governance", 12, 15)])

    b1, b2 = _overview(client, u1), _overview(client, u2)

    assert b1["evaluations"]["attempts"] == 5
    assert b1["evaluations"]["scored"] == 4
    assert b1["evaluations"]["average_score_pct"] == round((0.2 + 0.3 + 0.2 + 0.9) / 4, 4)
    assert b1["subjects"] == [{"subject": "Economy", "mastery": round(0.7 / 3, 4), "attempts": 3}]
    assert b1["weak_subjects"] == b1["subjects"]
    assert b1["revision"]["subjects"] == [
        {"subject": "Economy", "next_due": "2026-01-11", "due": True}
    ]
    assert b1["revision"]["due"] == 1 and b1["revision"]["total"] == 1

    assert b2["evaluations"]["attempts"] == 1
    assert b2["evaluations"]["by_mode"]["basic"]["attempts"] == 0
    assert b2["evaluations"]["average_score_pct"] == 0.8
    assert b2["subjects"] == [{"subject": "Polity & Governance", "mastery": 0.8, "attempts": 1}]
    assert b2["weak_subjects"] == []
    assert [s["subject"] for s in b2["revision"]["subjects"]] == ["Polity & Governance"]
    assert "Economy" not in str(b2)
    assert "Polity" not in str(b1)


def test_load_attempts_is_caller_only_bounded_and_newest_first(make_user):
    u1, u2 = make_user("c2-load1"), make_user("c2-load2")
    _insert(u1.id, [("basic", "Economy", s, 10) for s in (1, 2, 3)])
    _insert(u2.id, [("basic", "Ethics", 9, 10)])
    with SessionLocal() as db:
        rows = eh.load_attempts(db, u1.id, limit=2)
        assert [r.score for r in rows] == [3, 2]
        assert {r.subject for r in eh.load_attempts(db, u1.id)} == {"Economy"}
        with pytest.raises(ValueError):
            eh.load_attempts(db, "")


def test_analytics_failure_keeps_mentor_stats(client, make_user, monkeypatch, caplog):
    user = make_user("c2-fail")

    def _boom(db, user_id, limit=eh.ANALYTICS_ATTEMPT_LIMIT):
        raise RuntimeError("SELECT secret_column FROM evaluation_attempts host=db.internal")

    monkeypatch.setattr(progress_routes.evaluation_history, "load_attempts", _boom)
    with caplog.at_level(logging.WARNING, logger=progress_routes.__name__):
        body = _overview(client, user)
    assert body["evaluations"]["attempts"] == 0
    assert body["revision"] == {"due": 0, "total": 0, "subjects": []}
    assert len(body["streak"]["activity"]) == progress_routes._ACTIVITY_DAYS
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "progress_evaluation_analytics_failed (RuntimeError)" in logged
    assert "secret_column" not in logged and "db.internal" not in logged


def test_evaluation_through_the_api_shows_on_the_overview(client, make_user, monkeypatch):
    text = "## Score: 3/10\n\n### What you did well\n- Clear introduction\n"
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _p: AIMessage(content=text))
    )
    user, other = make_user("c2-e2e"), make_user("c2-e2e-other")
    answer = " ".join(["The Finance Commission devolves central taxes to the states."] * 8)
    for _ in range(3):
        r = client.post(
            "/api/v1/evaluator/evaluate/sync",
            json={
                "question": "Explain fiscal federalism in India today.",
                "answer": answer,
                "subject": "Economy",
            },
            headers=user.headers,
        )
        assert r.status_code == 200
    body = _overview(client, user)
    assert body["evaluations"]["scored"] == 3
    assert body["evaluations"]["average_score_pct"] == 0.3
    assert body["weak_subjects"] == [{"subject": "Economy", "mastery": 0.3, "attempts": 3}]
    assert body["revision"]["total"] == 1
    assert _overview(client, other)["evaluations"]["attempts"] == 0
