"""Offline tests for the free-first live-search provider chain.

``search_web`` (src/core/web_search.py) tries Tavily first (only when a key is
configured), then DDGS news, DDGS text, Google News RSS and PIB RSS. It must
fall through whenever a provider gives nothing usable - no API key, an error, a
timeout or zero results - stay inside one overall time budget, and contact
nothing when web search is disabled. Every provider is a fake; no network.
"""

from __future__ import annotations

import time

import pytest

import src.agents.mentor.graph as mg
import src.core.web_search as ws
from src.core.config import settings


def _hit(url, title="Title", provider="fake", published=None):
    return ws.make_result(
        title=title, url=url, snippet="snippet", provider=provider, published=published
    )


class _Recorder:
    """Fake provider factory that records which providers ran, with what query."""

    def __init__(self):
        self.calls = []

    def provider(self, name, results=(), exc=None, delay=0.0):
        def _fn(query, timeout):
            self.calls.append((name, query))
            if delay:
                time.sleep(delay)
            if exc:
                raise exc
            return list(results)

        return _fn

    @property
    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def chain(monkeypatch):
    """Enable search and replace all five providers with recorders."""
    monkeypatch.setattr(settings, "web_search_enabled", True)
    rec = _Recorder()

    def install(**providers):
        for name in ws.PROVIDER_ORDER:
            monkeypatch.setattr(ws, name, providers.get(name) or rec.provider(name))
        return rec

    return rec, install


A = "https://www.thehindu.com/a"
B = "https://pib.gov.in/PressReleasePage.aspx?PRID=1"
C = "https://indianexpress.com/c"


def test_tavily_results_are_used_without_fallback(chain):
    rec, install = chain
    install(_tavily=rec.provider("_tavily", [_hit(A), _hit(B)]))
    out = ws.search_web("prelims date")
    assert {r.url for r in out} == {A, B}
    assert rec.names == ["_tavily"]


def test_missing_tavily_key_falls_back(chain, monkeypatch):
    rec, install = chain
    # Real Tavily adapter with no key: the tool cannot be built -> [].
    monkeypatch.setattr(mg, "_search_tool", None)
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    install(_tavily=ws._tavily, _ddgs_news=rec.provider("_ddgs_news", [_hit(A), _hit(C)]))
    out = ws.search_web("prelims date")
    assert [r.url for r in out] == [A, C]
    assert rec.names == ["_ddgs_news"]


@pytest.mark.parametrize(
    "tavily_kwargs",
    [
        {"exc": RuntimeError("tavily 500")},  # provider error
        {"results": []},  # zero results
    ],
)
def test_tavily_error_or_empty_falls_back(chain, tavily_kwargs):
    rec, install = chain
    install(
        _tavily=rec.provider("_tavily", **tavily_kwargs),
        _ddgs_news=rec.provider("_ddgs_news", [_hit(A), _hit(C)]),
    )
    assert len(ws.search_web("prelims date")) == 2
    assert rec.names == ["_tavily", "_ddgs_news"]


def test_tavily_timeout_falls_back_quickly(chain, monkeypatch):
    rec, install = chain
    monkeypatch.setattr(ws, "PROVIDER_TIMEOUT_SECONDS", 0.05)
    install(
        _tavily=rec.provider("_tavily", [_hit(B)], delay=0.5),
        _ddgs_news=rec.provider("_ddgs_news", [_hit(A), _hit(C)]),
    )
    started = time.monotonic()
    out = ws.search_web("prelims date")
    assert {r.url for r in out} == {A, C}  # the hung provider's result is discarded
    assert time.monotonic() - started < 0.45


def test_results_without_urls_are_dropped_and_chain_continues(chain):
    rec, install = chain
    bad = [ws.make_result(title="t", url="", provider="x"), None]
    install(
        _tavily=rec.provider("_tavily", bad),
        _ddgs_news=rec.provider("_ddgs_news", [_hit(A), _hit(C)]),
    )
    assert [r.url for r in ws.search_web("q")] == [A, C]


def test_results_accumulate_across_providers_until_enough(chain):
    rec, install = chain
    install(
        _tavily=rec.provider("_tavily", [_hit(A)]),
        _ddgs_news=rec.provider("_ddgs_news", [_hit(A + "/"), _hit(C)]),  # A is a duplicate
    )
    out = ws.search_web("q")
    assert [r.url for r in out] == [A, C]
    assert rec.names == ["_tavily", "_ddgs_news"]  # stopped once 2 usable results


def test_all_providers_failing_returns_empty(chain):
    rec, install = chain
    install(**{n: rec.provider(n, exc=RuntimeError("down")) for n in ws.PROVIDER_ORDER})
    assert ws.search_web("q") == []
    assert rec.names == [n for n in ws.PROVIDER_ORDER]


def test_one_overall_time_budget_bounds_the_chain(chain, monkeypatch):
    rec, install = chain
    monkeypatch.setattr(ws, "TOTAL_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(ws, "PROVIDER_TIMEOUT_SECONDS", 0.2)
    install(**{n: rec.provider(n, delay=1.0) for n in ws.PROVIDER_ORDER})
    started = time.monotonic()
    assert ws.search_web("q") == []
    assert time.monotonic() - started < 0.8
    assert len(rec.calls) < len(ws.PROVIDER_ORDER)


def test_ddgs_not_installed_returns_empty(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "ddgs", None)  # import -> ImportError
    assert ws._ddgs_news("q", 1.0) == []
    assert ws._ddgs_text("q", 1.0) == []


def test_web_search_disabled_contacts_no_provider(chain, monkeypatch):
    rec, install = chain
    install()
    monkeypatch.setattr(settings, "web_search_enabled", False)
    assert ws.search_web("prelims date") == []
    assert mg._fetch_search_context("prelims date") == ""
    assert rec.calls == []


def test_queries_carry_no_year_or_suffix(chain):
    rec, install = chain
    install()
    ws.search_web("latest UPSC notification")
    assert rec.calls
    assert {q for _, q in rec.calls} == {"latest UPSC notification"}


def test_all_failing_tool_output_is_honest(chain):
    from src.graph.tools import web_search_with_sources

    rec, install = chain
    install(**{n: rec.provider(n, exc=TimeoutError("slow")) for n in ws.PROVIDER_ORDER})
    text, artifact = web_search_with_sources("UPSC prelims date")
    assert "No live web results found" in text
    assert "Do not answer dated facts from memory" in text
    assert artifact == {"sources": []}


def test_mentor_digest_wrapper_keeps_source_markers(chain):
    rec, install = chain
    install(_tavily=rec.provider("_tavily", [_hit(B, title="Notice"), _hit(A)]))
    digest = mg._fetch_search_context("q")
    assert "[1] Notice" in digest
    assert f"(Source: {B})" in digest
