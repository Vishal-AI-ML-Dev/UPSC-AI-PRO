"""Offline tests for the Mentor's live-search provider chain.

``_fetch_search_context`` (src/agents/mentor/graph.py) must try Tavily first and
fall back to the free, keyless DuckDuckGo (``ddgs``) search whenever Tavily
gives nothing usable: no API key, an error, a timeout, or zero results. No
network is touched - Tavily and DDGS are replaced with fakes.
"""

from __future__ import annotations

import sys
import time
import types

import pytest

import src.agents.mentor.graph as mg
from src.core.config import settings

DDG_RESULTS = [
    {
        "title": "Prelims notice",
        "body": "UPSC prelims notice text",
        "href": "https://upsc.gov.in/n",
    },
]


class _FakeTavily:
    def __init__(self, result=None, exc=None, delay=0.0):
        self.result = result
        self.exc = exc
        self.delay = delay
        self.queries = []

    def invoke(self, query):
        self.queries.append(query)
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


class _FakeDDGS:
    """Stands in for ``ddgs.DDGS``; records every query it receives."""

    queries: list = []
    results: list = DDG_RESULTS
    exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, **kwargs):
        type(self).queries.append(query)
        if type(self).exc:
            raise type(self).exc
        return list(type(self).results)


@pytest.fixture
def search_env(monkeypatch):
    """Enable web search, install a fake ``ddgs`` module, reset the Tavily cache."""
    monkeypatch.setattr(settings, "web_search_enabled", True)
    monkeypatch.setattr(mg, "_search_tool", None)
    _FakeDDGS.queries = []
    _FakeDDGS.results = DDG_RESULTS
    _FakeDDGS.exc = None
    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=_FakeDDGS))

    def _use_tavily(tool):
        monkeypatch.setattr(mg, "_get_search_tool", lambda: tool)
        return tool

    return _use_tavily


def test_tavily_results_are_used_without_fallback(search_env):
    tavily = search_env(
        _FakeTavily({"results": [{"title": "T", "content": "body", "url": "https://pib.gov.in/x"}]})
    )
    out = mg._fetch_search_context("prelims date")
    assert out == "- T: body (Source: https://pib.gov.in/x)"
    assert len(tavily.queries) == 1
    assert _FakeDDGS.queries == []


def test_missing_tavily_key_falls_back_to_ddgs(search_env, monkeypatch):
    # Real _get_search_tool with no key: Tavily init fails -> None.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(settings, "tavily_api_key", "")
    out = mg._fetch_search_context("prelims date")
    assert "(Source: https://upsc.gov.in/n)" in out
    assert len(_FakeDDGS.queries) == 1


def test_tavily_error_falls_back_to_ddgs(search_env):
    search_env(_FakeTavily(exc=RuntimeError("tavily 500")))
    out = mg._fetch_search_context("prelims date")
    assert "https://upsc.gov.in/n" in out


def test_tavily_timeout_falls_back_to_ddgs(search_env, monkeypatch):
    monkeypatch.setattr(mg, "SEARCH_TIMEOUT_SECONDS", 0.05)
    search_env(_FakeTavily({"results": []}, delay=0.5))
    started = time.monotonic()
    out = mg._fetch_search_context("prelims date")
    assert "https://upsc.gov.in/n" in out
    # The hung provider was abandoned, not waited for.
    assert time.monotonic() - started < 0.45


def test_tavily_empty_results_fall_back_to_ddgs(search_env):
    search_env(_FakeTavily({"results": []}))
    assert "https://upsc.gov.in/n" in mg._fetch_search_context("prelims date")


def test_tavily_results_without_urls_fall_back_to_ddgs(search_env):
    # An uncitable Tavily hit is useless for grounding -> treat as empty.
    search_env(_FakeTavily({"results": [{"title": "T", "content": "c", "url": ""}]}))
    assert "https://upsc.gov.in/n" in mg._fetch_search_context("prelims date")


def test_all_providers_failing_returns_empty(search_env):
    search_env(_FakeTavily(exc=RuntimeError("down")))
    _FakeDDGS.exc = RuntimeError("ratelimited")
    assert mg._fetch_search_context("prelims date") == ""


def test_ddgs_not_installed_returns_empty(search_env, monkeypatch):
    search_env(None)
    monkeypatch.setitem(sys.modules, "ddgs", None)  # import -> ImportError
    assert mg._fetch_search_context("prelims date") == ""


def test_web_search_disabled_contacts_no_provider(search_env, monkeypatch):
    tavily = search_env(_FakeTavily({"results": []}))
    monkeypatch.setattr(settings, "web_search_enabled", False)
    assert mg._fetch_search_context("prelims date") == ""
    assert tavily.queries == []
    assert _FakeDDGS.queries == []


def test_queries_carry_no_hard_coded_year(search_env):
    tavily = search_env(_FakeTavily({"results": []}))
    mg._fetch_search_context("latest UPSC notification")
    sent = tavily.queries + _FakeDDGS.queries
    assert len(sent) == 2
    for query in sent:
        assert query.startswith("latest UPSC notification")
        assert not any(tok.isdigit() and len(tok) == 4 for tok in query.split())


def test_all_failing_tool_output_is_honest(search_env):
    """End to end through the tool: no live data -> explicit no-results notice."""
    from src.graph.tools import web_search_tool_fn

    search_env(_FakeTavily(exc=TimeoutError("slow")))
    _FakeDDGS.results = []
    out = web_search_tool_fn("UPSC prelims date")
    assert "No live web results found" in out
    assert "Do not answer dated facts from memory" in out
