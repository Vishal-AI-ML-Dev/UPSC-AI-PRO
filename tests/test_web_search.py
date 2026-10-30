"""Offline tests for structured web search (src/core/web_search.py).

Provider adapters are fed recorded/fake payloads (Tavily dict, DDGS rows,
Google News and PIB RSS XML), and the pure helpers - date parsing, official
classification, URL cleaning, de-duplication, ranking, text sanitising and the
model-facing digest - are checked directly. No network.
"""

from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

import src.agents.mentor.graph as mg
import src.core.web_search as ws
from src.core.prompt_safety import _FENCE_BEGIN


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
class _FakeTavily:
    def __init__(self, payload):
        self.payload = payload
        self.queries = []

    def invoke(self, query):
        self.queries.append(query)
        return self.payload


def test_tavily_adapter_normalises_results(monkeypatch):
    tool = _FakeTavily(
        {
            "results": [
                {
                    "title": "Prelims notice",
                    "url": "https://upsc.gov.in/notice?utm_source=x",
                    "content": "<b>Notice</b> text",
                    "published_date": "2026-10-20",
                },
                {"title": "Explainer", "url": "https://www.thehindu.com/x", "content": "c"},
                {"title": "Bad", "url": "javascript:alert(1)", "content": "c"},
            ]
        }
    )
    monkeypatch.setattr(mg, "_get_search_tool", lambda: tool)
    out = ws._tavily("prelims notice", 5)
    assert tool.queries == ["prelims notice"]  # no suffix, no year
    assert len(out) == 3 and out[2] is None  # unsafe URL rejected
    first, second = out[0], out[1]
    assert first.url == "https://upsc.gov.in/notice"
    assert first.publisher == "Union Public Service Commission"
    assert first.published == "2026-10-20"
    assert first.official is True
    assert first.snippet == "Notice text"
    assert first.provider == "tavily"
    assert second.publisher == "The Hindu"
    assert second.published == ""  # Tavily general results carry no date
    assert second.official is False


def test_tavily_adapter_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(mg, "_get_search_tool", lambda: None)
    assert ws._tavily("q", 5) == []


class _FakeDDGS:
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def news(self, query, **kwargs):
        _FakeDDGS.calls.append(("news", query, kwargs))
        return [
            {
                "date": "2026-10-28T09:00:00+00:00",
                "title": "RBI keeps repo rate unchanged",
                "body": "The MPC ...",
                "url": "https://www.livemint.com/economy/rbi",
                "source": "Mint",
            }
        ]

    def text(self, query, **kwargs):
        _FakeDDGS.calls.append(("text", query, kwargs))
        return [{"title": "RBI", "href": "https://rbi.org.in/home", "body": "Bank"}]


@pytest.fixture
def fake_ddgs(monkeypatch):
    _FakeDDGS.calls = []
    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=_FakeDDGS))


def test_ddgs_news_adapter_keeps_date_and_publisher(fake_ddgs):
    (r,) = ws._ddgs_news("repo rate", 5)
    assert r.title == "RBI keeps repo rate unchanged"
    assert r.published == "2026-10-28"
    assert r.publisher == "Mint"
    assert r.provider == "ddgs_news"
    kind, query, kwargs = _FakeDDGS.calls[0]
    assert (kind, query) == ("news", "repo rate")
    assert kwargs["region"] == "in-en" and kwargs["max_results"] == ws.MAX_RESULTS


def test_ddgs_text_adapter_has_no_date(fake_ddgs):
    (r,) = ws._ddgs_text("rbi", 5)
    assert r.url == "https://rbi.org.in/home"
    assert r.published == ""
    assert r.official is True
    assert r.publisher == "Reserve Bank of India"


GOOGLE_NEWS_XML = b"""<?xml version="1.0"?><rss><channel>
<item>
  <title>Cabinet approves new scheme - Press Information Bureau</title>
  <link>https://news.google.com/rss/articles/abc?oc=5</link>
  <pubDate>Tue, 27 Oct 2026 10:00:00 GMT</pubDate>
  <description>&lt;a href="x"&gt;Cabinet approves&lt;/a&gt;</description>
  <source url="https://pib.gov.in">Press Information Bureau</source>
</item>
<item>
  <title>Undated story - The Hindu</title>
  <link>https://news.google.com/rss/articles/def</link>
  <source url="https://www.thehindu.com">The Hindu</source>
</item>
</channel></rss>"""


def test_google_news_rss_adapter(monkeypatch):
    requested = []
    monkeypatch.setattr(
        ws, "_http_get", lambda url, timeout: requested.append(url) or GOOGLE_NEWS_XML
    )
    first, second = ws._google_news_rss("new scheme cabinet", 5)
    assert "q=new%20scheme%20cabinet" in requested[0]
    assert first.title == "Cabinet approves new scheme"  # " - Publisher" suffix removed
    assert first.url == "https://news.google.com/rss/articles/abc?oc=5"
    assert first.publisher == "Press Information Bureau"
    assert first.official is True  # decided by the publisher's own site
    assert first.published == "2026-10-27"
    assert first.snippet == "Cabinet approves"
    assert second.published == ""
    assert second.official is False


PIB_XML = b"""<?xml version="1.0"?><rss><channel>
<item><title>Cabinet approves PM Surya Ghar solar scheme expansion</title>
<link>https://pib.gov.in/PressReleasePage.aspx?PRID=1</link>
<pubDate>Wed, 28 Oct 2026 18:30:00 +0530</pubDate>
<description>Rooftop solar for households</description></item>
<item><title>Defence minister visits Ladakh</title>
<link>https://pib.gov.in/PressReleasePage.aspx?PRID=2</link>
<pubDate>Wed, 28 Oct 2026 12:00:00 +0530</pubDate></item>
</channel></rss>"""


def test_pib_rss_adapter_keeps_only_matching_releases(monkeypatch):
    monkeypatch.setattr(ws, "_http_get", lambda url, timeout: PIB_XML)
    out = ws._pib_rss("PM Surya Ghar rooftop solar scheme", 5)
    assert [r.url for r in out] == ["https://pib.gov.in/PressReleasePage.aspx?PRID=1"]
    assert out[0].official is True
    assert out[0].publisher == "Press Information Bureau"
    assert out[0].published == "2026-10-28"


def test_pib_rss_adapter_without_keywords_skips_fetch(monkeypatch):
    monkeypatch.setattr(ws, "_http_get", lambda *a: pytest.fail("must not fetch"))
    assert ws._pib_rss("the latest news", 5) == []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-28", "2026-10-28"),
        ("2026-10-28T09:00:00Z", "2026-10-28"),
        ("2026-10-28T09:00:00+05:30", "2026-10-28"),
        ("Tue, 27 Oct 2026 10:00:00 GMT", "2026-10-27"),
        (date(2026, 1, 5), "2026-01-05"),
        ("", ""),
        (None, ""),
        ("yesterday", ""),
        ("28/10/2026", ""),
        ("1900-01-01", ""),
    ],
)
def test_parse_date_never_guesses(value, expected):
    assert ws.parse_date(value) == expected


def test_parse_date_rejects_future_dates():
    future = (date.today() + timedelta(days=30)).isoformat()
    assert ws.parse_date(future) == ""


def test_display_date():
    assert ws.display_date("2026-10-28") == "28 Oct 2026"
    assert ws.display_date("") == "date not available"


@pytest.mark.parametrize(
    "url, official",
    [
        ("https://pib.gov.in/x", True),
        ("https://upsc.gov.in", True),
        ("https://www.india.gov.in/x", True),
        ("https://indiacode.nic.in/x", True),
        ("https://rbi.org.in/x", True),
        ("https://www.un.org/x", True),
        ("https://pib.gov.in.evil.com/x", False),  # lookalike
        ("https://notgov.in/x", False),
        ("https://www.thehindu.com/x", False),
        ("https://drishtiias.com/x", False),
    ],
)
def test_official_classification(url, official):
    assert ws.is_official(url) is official


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://Example.com/a?utm_source=x&id=3#frag", "https://example.com/a?id=3"),
        ("https://example.com/a?id=3&b=4", "https://example.com/a?id=3&b=4"),
        ("https://example.com/wiki/Foo_(bar)", "https://example.com/wiki/Foo_%28bar%29"),
        ("https://example.com/a b", "https://example.com/a%20b"),
        ("javascript:alert(1)", ""),
        ("ftp://example.com/x", ""),
        ("not a url", ""),
        ("https://exa\nmple.com", ""),
        ("", ""),
    ],
)
def test_clean_url(url, expected):
    assert ws.clean_url(url) == expected


def test_dedupe_by_normalised_url():
    hits = [
        ws.make_result(title="a", url="https://www.thehindu.com/x/", provider="p"),
        ws.make_result(title="b", url="https://thehindu.com/x?utm_medium=y", provider="p"),
        ws.make_result(title="c", url="https://thehindu.com/y", provider="p"),
        None,
    ]
    assert [r.title for r in ws.dedupe(hits)] == ["a", "c"]


def test_rank_official_then_dated_then_provider_order():
    secondary_undated = ws.make_result(title="s1", url="https://a.com/1", provider="p")
    secondary_dated = ws.make_result(
        title="s2", url="https://a.com/2", provider="p", published="2026-10-01"
    )
    official = ws.make_result(title="o", url="https://pib.gov.in/1", provider="p")
    ranked = ws.rank([secondary_undated, secondary_dated, official])
    assert [r.title for r in ranked] == ["o", "s2", "s1"]


def test_clean_text_sanitises_untrusted_snippets():
    text = ws.clean_text(
        f"<b>Hi</b>\n\n there {_FENCE_BEGIN} (Source: https://evil.example) " + "x" * 400, 120
    )
    assert "<b>" not in text and "\n" not in text
    assert _FENCE_BEGIN not in text
    assert "(Source:" not in text
    assert len(text) <= 120


def test_search_result_roundtrip_and_validation():
    r = ws.make_result(
        title="T", url="https://pib.gov.in/x", provider="tavily", published="2026-10-01"
    )
    assert ws.SearchResult.from_dict(r.to_dict()) == r
    assert ws.SearchResult.from_dict({"url": "javascript:x"}) is None
    assert ws.SearchResult.from_dict("nope") is None
    # official is recomputed from the URL, never trusted from the payload
    forged = dict(r.to_dict(), url="https://evil.example/x", official=True)
    assert ws.SearchResult.from_dict(forged).official is False


def test_format_digest_numbers_dates_and_kinds():
    official = ws.make_result(
        title="Notice", url="https://pib.gov.in/1", provider="p", published="2026-10-28"
    )
    other = ws.make_result(title="Story", url="https://thehindu.com/2", provider="p")
    digest = ws.format_digest([official, other], [3, 4])
    assert digest.splitlines()[0] == (
        "[3] Notice — Press Information Bureau — official source — "
        "published 28 Oct 2026 (Source: https://pib.gov.in/1)"
    )
    assert "[4] Story — The Hindu — secondary source — date not available" in digest


def test_search_disabled_is_default_in_tests():
    # Safety net (conftest): nothing may hit the network unless a test opts in.
    assert ws.search_web("anything") == []


def test_aggregator_result_keeps_official_flag_through_artifacts(monkeypatch):
    """A Google News link to a PIB release stays official after (de)serialising."""
    from src.core.grounding import web_sources_from_tool_messages

    monkeypatch.setattr(ws, "_http_get", lambda url, timeout: GOOGLE_NEWS_XML)
    first = ws._google_news_rss("cabinet scheme", 5)[0]
    assert first.official is True
    rebuilt = ws.SearchResult.from_dict(first.to_dict())
    assert rebuilt == first
    msg = types.SimpleNamespace(
        type="tool",
        name="web_search",
        content="",
        artifact={"sources": [{**first.to_dict(), "n": 1}]},
    )
    (source,) = web_sources_from_tool_messages([msg])
    assert source["official"] is True
    assert source["publisher"] == "Press Information Bureau"
