"""Daily current affairs: no mislabelled dates, no model-made news (mocked).

The news feeds only return the latest headlines (``fetch_headlines`` has no
date), so a past date must not get today's headlines under its label, and with
no headlines the model must not be asked to write "news" from memory.
"""

from datetime import timedelta

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

import src.agents.current_affairs.graph as ca
import src.agents.current_affairs.ingest as ingest

API = "/api/v1"
HEADLINES = "PIB: Cabinet approves scheme X - short snippet"


def _label(d):
    return d.strftime("%d %B %Y")


@pytest.fixture
def model(monkeypatch):
    """Record model calls; stream a fixed digest."""
    calls = []

    def stream(inputs):
        for value in inputs:
            calls.append(value.to_string())
        yield AIMessageChunk(content="DIGEST")

    monkeypatch.setattr(ca, "get_llm", lambda: RunnableGenerator(stream))
    return calls


def _headlines(monkeypatch, text):
    fetched = []

    def fake(**kw):
        fetched.append(kw)
        return text

    monkeypatch.setattr(ingest, "fetch_headlines", fake)
    return fetched


def test_today_with_headlines_generates(model, monkeypatch):
    _headlines(monkeypatch, HEADLINES)
    body = "".join(ca.get_daily_ca(_label(ca._today_ist())))
    assert body.endswith("DIGEST")
    assert len(model) == 1


def test_blank_date_means_today(model, monkeypatch):
    _headlines(monkeypatch, HEADLINES)
    body = "".join(ca.get_daily_ca(""))
    assert body.endswith("DIGEST")
    assert _label(ca._today_ist()) in model[0]


@pytest.mark.parametrize("days_back", [1, 7, 29])
def test_past_date_does_not_show_todays_headlines(model, monkeypatch, days_back):
    fetched = _headlines(monkeypatch, HEADLINES)
    past = _label(ca._today_ist() - timedelta(days=days_back))
    body = "".join(ca.get_daily_ca(past))
    assert model == []  # no generation for a date the feeds cannot cover
    assert fetched == []
    assert "DIGEST" not in body
    assert past in body and "not available" in body
    assert _label(ca._today_ist()) in body


def test_unreadable_date_is_not_generated(model, monkeypatch):
    _headlines(monkeypatch, HEADLINES)
    body = "".join(ca.get_daily_ca("someday"))
    assert model == []
    assert "not available" in body


def test_no_headlines_means_no_model_news(model, monkeypatch):
    _headlines(monkeypatch, "")
    body = "".join(ca.get_daily_ca(_label(ca._today_ist())))
    assert model == []
    assert "DIGEST" not in body
    assert "Live news feed unavailable" in body
    assert "no current affairs were generated" in body


def test_headline_fetch_error_means_no_model_news(model, monkeypatch):
    def boom(**kw):
        raise RuntimeError("feed down")

    monkeypatch.setattr(ingest, "fetch_headlines", boom)
    body = "".join(ca.get_daily_ca(_label(ca._today_ist())))
    assert model == []
    assert "no current affairs were generated" in body


def test_daily_endpoint_past_date(client, make_user, model, monkeypatch):
    _headlines(monkeypatch, HEADLINES)
    user = make_user("cadate")
    past = _label(ca._today_ist() - timedelta(days=3))
    r = client.post(f"{API}/current-affairs/daily", json={"date": past}, headers=user.headers)
    assert r.status_code == 200
    assert model == []
    assert "not available" in r.text
