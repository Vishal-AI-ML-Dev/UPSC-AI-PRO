"""Planner exam-date handling (PL1-PL3 of the 2026-10-29 prompt audit), mocked.

Web search is a fake Tavily tool, the knowledge base is off, the model is a
stand-in that records the prompt, and "today" is pinned to 29 October 2026.
"""

from datetime import datetime

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

import src.agents.planner.graph as planner
from src.core.config import settings
from src.core.plan_timeline import compute_plan_timeline

TODAY = datetime(2026, 10, 29, 10, 0)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return TODAY


class _Tool:
    """Fake Tavily: returns the snippet registered for the year in the query."""

    def __init__(self, by_year):
        self.by_year = by_year

    def invoke(self, query):
        for year, content in self.by_year.items():
            if str(year) in query:
                return [{"content": content}]
        return []


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(planner, "_upsc_date_cache", {"date": None, "timestamp": 0})
    monkeypatch.setattr(planner, "_duckduckgo_date_search", lambda year: "")

    def use(by_year):
        monkeypatch.setattr(planner, "_get_search_tool", lambda: _Tool(by_year))

    return use


# ----------------------------- PL1: cache per year -------------------------- #
def test_cached_date_does_not_leak_to_another_year(web):
    web({2026: "UPSC Prelims 2026 will be held on 24 May 2026."})
    assert planner._fetch_upsc_prelims_date(2026) == "24 May 2026"
    assert planner._fetch_upsc_prelims_date(2027) == ""


def test_cache_still_reused_for_the_same_year(web):
    web({2026: "UPSC Prelims 2026 will be held on 24 May 2026."})
    assert planner._fetch_upsc_prelims_date(2026) == "24 May 2026"
    web({})  # a second lookup for the same year must come from the cache
    assert planner._fetch_upsc_prelims_date(2026) == "24 May 2026"


# ----------------------------- PL2: only the target year -------------------- #
def test_date_for_another_year_is_ignored(web):
    web({2027: "Recap: UPSC Prelims 2026 was held on 24 May 2026. Dates for 2027 awaited."})
    assert planner._fetch_upsc_prelims_date(2027) == ""


def test_target_year_date_found_after_an_older_one(web):
    web({2027: "Prelims 2026 was held on 24 May 2026; Prelims 2027 is on 23 May 2027."})
    assert planner._fetch_upsc_prelims_date(2027) == "23 May 2027"


# ----------------------------- prompt checks -------------------------------- #
@pytest.fixture
def plan_prompt(monkeypatch):
    """Run generate_plan with a pinned date; return the prompt the model got."""
    monkeypatch.setattr(planner, "datetime", _FixedDatetime)
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: False)
    seen = []

    def stream(inputs):
        for value in inputs:
            seen.append(value.to_string())
        yield AIMessageChunk(content="PLAN")

    monkeypatch.setattr(planner, "get_llm", lambda: RunnableGenerator(stream))

    def run(goal, live_date=""):
        monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: live_date)
        out = "".join(planner.generate_plan(goal, "6", "", "", "1"))
        assert out == "PLAN"
        return seen[-1]

    return run


def test_web_date_is_not_labelled_official(plan_prompt):
    prompt = plan_prompt("UPSC 2027", live_date="23 May 2027")
    assert "fetched from official sources" not in prompt
    assert "from official source" not in prompt
    assert "23 May 2027" in prompt
    assert "not verified" in prompt


def test_elapsed_target_year_is_not_this_month(plan_prompt):
    prompt = plan_prompt("UPSC 2026")
    assert "Prelims is this month" not in prompt
    assert "Prelims 2026 has already taken place" in prompt
    assert "Target Prelims    : 2027" in prompt


def test_elapsed_live_date_rolls_forward(plan_prompt):
    prompt = plan_prompt("UPSC 2026", live_date="24 May 2026")
    assert "Prelims is this month" not in prompt
    assert "Target Prelims    : 2027" in prompt
    assert "24 May 2026" not in prompt  # last year's date is not shown as the target


def test_future_target_unchanged(plan_prompt):
    prompt = plan_prompt("UPSC 2027")
    assert "Target Prelims    : 2027 (8 months left)" in prompt
    assert "already taken place" not in prompt


# ----------------------------- timeline helper (sync meta) ------------------ #
def test_helper_rolls_elapsed_heuristic_year():
    meta = compute_plan_timeline("UPSC 2026", today=TODAY, live_date=None)
    assert meta.attempt_year == 2027
    assert meta.months_left == 8
    assert "Prelims 2026 has already taken place" in meta.timeline_msg
    assert meta.prelims_date is None


def test_helper_june_of_target_year_is_still_this_month():
    meta = compute_plan_timeline("UPSC 2026", today=datetime(2026, 6, 10), live_date=None)
    assert meta.attempt_year == 2026
    assert meta.months_left == 0
    assert "this month" in meta.timeline_msg.lower()
