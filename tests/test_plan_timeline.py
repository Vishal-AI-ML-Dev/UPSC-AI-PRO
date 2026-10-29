"""Offline tests for pure study-plan timeline helpers (no LLM / network)."""

from datetime import datetime

from src.core.plan_timeline import (
    compute_plan_timeline,
    parse_attempt_year,
    timeline_message,
)
from src.schemas import StudyPlanMeta


def test_parse_attempt_year():
    assert parse_attempt_year("UPSC 2027", today=datetime(2026, 1, 1)) == 2027
    assert parse_attempt_year("no year here", today=datetime(2026, 1, 1)) == 2027
    assert parse_attempt_year("", today=datetime(2026, 7, 1)) == 2027


def test_timeline_message_bands():
    assert "this month" in timeline_message(0).lower()
    assert "under 2 months" in timeline_message(2).lower()
    assert "under 4 months" in timeline_message(4).lower()
    assert "under 6 months" in timeline_message(6).lower()
    assert "1 year" in timeline_message(10).lower()
    assert "1+ years" in timeline_message(20).lower()


def test_compute_plan_timeline_heuristic_no_live_date():
    meta = compute_plan_timeline("UPSC 2026", today=datetime(2026, 1, 1), live_date=None)
    assert isinstance(meta, StudyPlanMeta)
    assert meta.attempt_year == 2026
    assert meta.months_left == 5
    assert meta.prelims_date is None


def test_compute_plan_timeline_with_live_date():
    meta = compute_plan_timeline("UPSC 2026", today=datetime(2026, 1, 1), live_date="24 May 2026")
    assert meta.months_left == 4
    assert meta.prelims_date == "24 May 2026"


def test_compute_plan_timeline_past_date_rolls_to_next_prelims():
    # Was: months_left clamped to 0 -> "Prelims is this month" for an exam that
    # had already happened (2026-10-29 audit PL3). Now the plan targets 2027.
    meta = compute_plan_timeline("UPSC 2026", today=datetime(2026, 7, 1), live_date="24 May 2026")
    assert meta.attempt_year == 2027
    assert meta.months_left == 11
    assert "Prelims 2026 has already taken place" in meta.timeline_msg
    assert meta.prelims_date is None
