"""Setup safety: web-search switch, Gemini output-token limit, email/log safety.

Every provider is replaced by a recorder, so nothing here contacts Tavily,
DuckDuckGo, Gemini or an SMTP server.
"""

import asyncio
import logging
import sys
import types
import uuid

import pytest
from pydantic import ValidationError

from src.core.config import Settings, settings

AUTH = "/api/v1/auth"


# --------------------------------------------------------------------------- #
# Recorders
# --------------------------------------------------------------------------- #
class _Calls(list):
    def hit(self, name):
        self.append(name)


def _fake_ddgs_module(calls: _Calls) -> types.ModuleType:
    class DDGS:
        def __init__(self, *a, **k):
            calls.hit("ddgs")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, *a, **k):
            calls.hit("ddgs.text")
            return [{"title": "t", "body": "Prelims 24 May 2026", "href": "https://upsc.gov.in"}]

        def news(self, *a, **k):
            calls.hit("ddgs.news")
            return [{"title": "t", "body": "b"}]

    mod = types.ModuleType("fake_ddgs")
    mod.DDGS = DDGS
    return mod


class _FakeTavilyTool:
    def __init__(self, calls: _Calls):
        self.calls = calls

    def invoke(self, *a, **k):
        self.calls.hit("tavily")
        return {"results": [{"title": "t", "content": "c", "url": "https://upsc.gov.in"}]}


@pytest.fixture
def search_recorders(monkeypatch):
    """Route every Tavily/DuckDuckGo entry point to a call recorder."""
    import urllib.request

    import src.agents.mentor.graph as mentor_graph
    import src.agents.planner.graph as planner_graph

    calls = _Calls()
    fake = _fake_ddgs_module(calls)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake)
    monkeypatch.setitem(sys.modules, "ddgs", fake)
    monkeypatch.setattr(mentor_graph, "_get_search_tool", lambda: _FakeTavilyTool(calls))
    monkeypatch.setattr(planner_graph, "_get_search_tool", lambda: _FakeTavilyTool(calls))
    monkeypatch.setitem(planner_graph._upsc_date_cache, "date", None)
    monkeypatch.setitem(planner_graph._upsc_date_cache, "timestamp", 0)

    def _urlopen(*a, **k):
        calls.hit("tavily.http")
        raise OSError("blocked in test")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(settings, "tavily_api_key", "dummy-not-a-real-key")
    return calls


def _run_every_search_path():
    from src.agents.current_affairs import monthly_ingest
    from src.agents.mentor.graph import _duckduckgo_search, _fetch_search_context
    from src.agents.planner.graph import _fetch_upsc_prelims_date
    from src.graph.tools import web_search_tool_fn

    return {
        "mentor": _fetch_search_context("prelims date"),
        "mentor_ddg": _duckduckgo_search("prelims date"),
        "tool": web_search_tool_fn("prelims date"),
        "planner": _fetch_upsc_prelims_date(2026),
        "monthly_tavily": monthly_ingest._tavily("q", "2026-01-01", "2026-01-31", 4, 5),
        "monthly_ddg": monthly_ingest._duckduckgo("q", 1, 2026, 4, 5),
    }


# --------------------------------------------------------------------------- #
# 1. Web search switch
# --------------------------------------------------------------------------- #
def test_web_search_disabled_contacts_no_provider(monkeypatch, search_recorders):
    monkeypatch.setattr(settings, "web_search_enabled", False)
    out = _run_every_search_path()
    assert search_recorders == []
    assert out["mentor"] == "" and out["mentor_ddg"] == "" and out["planner"] == ""
    assert out["monthly_tavily"] == [] and out["monthly_ddg"] == []
    assert "No live web results" in out["tool"]


def test_web_search_enabled_reaches_recorders(monkeypatch, search_recorders):
    """Control: the same paths DO reach the (fake) providers when enabled."""
    monkeypatch.setattr(settings, "web_search_enabled", True)
    _run_every_search_path()
    assert "tavily" in search_recorders
    assert "ddgs.text" in search_recorders
    assert "tavily.http" in search_recorders
    assert "ddgs.news" in search_recorders


# --------------------------------------------------------------------------- #
# 2. Gemini output-token limit
# --------------------------------------------------------------------------- #
class _FakeGemini:
    built: list = []

    def __init__(self, **kwargs):
        _FakeGemini.built.append(kwargs)

    def with_config(self, *a, **k):
        return self

    def with_fallbacks(self, *a, **k):
        return self


def test_every_gemini_model_gets_the_token_limit(monkeypatch):
    from src.core import llm

    _FakeGemini.built = []
    monkeypatch.setattr(llm, "_GuardedGemini", _FakeGemini)
    monkeypatch.setattr(settings, "google_api_key", "dummy-not-a-real-key")
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "llm_max_output_tokens", 1234)
    llm.reset_llm()
    try:
        llm.get_llm()
        llm.get_fast_llm()
    finally:
        llm.reset_llm()
    # main chain: flash + flash-lite fallback; fast chain: flash-lite + flash fallback
    assert len(_FakeGemini.built) == 4
    assert all(k["max_output_tokens"] == 1234 for k in _FakeGemini.built)
    assert {k["model"] for k in _FakeGemini.built} == {settings.llm_model, settings.llm_fast_model}


def test_real_gemini_class_accepts_the_limit():
    """Construction only (no request): the installed class keeps the value."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = ChatGoogleGenerativeAI(
        model=settings.llm_model, google_api_key="dummy-not-a-real-key", max_output_tokens=321
    )
    assert model.max_output_tokens == 321


@pytest.mark.parametrize("bad", [0, -1, 65537, "abc", ""])
def test_invalid_token_limit_is_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(llm_max_output_tokens=bad)


@pytest.mark.parametrize("good", [1, 4096, 65536])
def test_valid_token_limit_is_accepted(good):
    assert Settings(llm_max_output_tokens=good).llm_max_output_tokens == good


def test_invalid_token_limit_from_env_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "0")
    with pytest.raises(ValidationError):
        Settings()


# --------------------------------------------------------------------------- #
# 3. Email: no links/tokens in logs, production fails safely
# --------------------------------------------------------------------------- #
def _no_smtp(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "smtp_password", "")


def _production(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    assert settings.is_production


class _CaptureSMTP:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _CaptureSMTP.sent.append(msg)


@pytest.mark.parametrize("smtp_on", [False, True])
@pytest.mark.parametrize("kind", ["reset", "verify"])
def test_email_helpers_never_log_link_or_token(monkeypatch, caplog, smtp_on, kind):
    from src.core import email_utils

    token = "tok" + uuid.uuid4().hex
    link = f"http://127.0.0.1:5173/?{kind}_token={token}"
    _CaptureSMTP.sent = []
    monkeypatch.setattr(email_utils.smtplib, "SMTP", _CaptureSMTP)
    if smtp_on:
        monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
        monkeypatch.setattr(settings, "smtp_user", "smoke@test.local")
        monkeypatch.setattr(settings, "smtp_password", "not-real")
    else:
        _no_smtp(monkeypatch)
    monkeypatch.setattr(settings, "env", "test")
    send = email_utils.send_reset_email if kind == "reset" else email_utils.send_verification_email
    with caplog.at_level(logging.DEBUG):
        send("someone@example.com", link)
    assert token not in caplog.text and link not in caplog.text
    if smtp_on:  # capture workflow intact: the link is in the (captured) email only
        assert len(_CaptureSMTP.sent) == 1 and token in _CaptureSMTP.sent[0].as_string()
    else:
        assert _CaptureSMTP.sent == []
        assert "link not logged" in caplog.text


def test_api_flows_never_log_raw_tokens(client, monkeypatch, caplog):
    """Real register/forgot/resend routes, SMTP captured or missing (non-production)."""
    import src.api.routes.auth as auth_mod
    from src.core import email_utils

    # Pin non-production: CI has no .env, and empty ENV + DEBUG=false counts as production.
    monkeypatch.setattr(settings, "env", "test")
    raw_tokens = []

    def _wrap(fn):
        def inner(db, user_id):
            raw = fn(db, user_id)
            raw_tokens.append(raw)
            return raw

        return inner

    monkeypatch.setattr(auth_mod, "create_reset_token", _wrap(auth_mod.create_reset_token))
    monkeypatch.setattr(
        auth_mod, "create_verification_token", _wrap(auth_mod.create_verification_token)
    )
    monkeypatch.setattr(email_utils.smtplib, "SMTP", _CaptureSMTP)
    email = f"logs_{uuid.uuid4().hex[:10]}@example.com"
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"{AUTH}/register", json={"email": email, "password": "secret123"})
        assert r.status_code == 201 and r.json().get("verification_required") is True
        assert client.post(f"{AUTH}/resend-verification", json={"email": email}).status_code == 200
        assert client.post(f"{AUTH}/forgot-password", json={"email": email}).status_code == 200
        _no_smtp(monkeypatch)
        assert client.post(f"{AUTH}/forgot-password", json={"email": email}).status_code == 200
    assert len(raw_tokens) == 4
    for raw in raw_tokens:
        assert raw not in caplog.text
    assert "_token=" not in caplog.text


def test_production_without_smtp_fails_startup(monkeypatch):
    from src.api.main import app, lifespan
    from src.core.email_utils import check_email_config

    _production(monkeypatch)
    _no_smtp(monkeypatch)
    monkeypatch.setattr(settings, "require_email_verification", True)
    with pytest.raises(RuntimeError, match="SMTP is not configured"):
        check_email_config()

    async def _start():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="SMTP is not configured"):
        asyncio.run(_start())


def test_production_with_smtp_passes_startup_check(monkeypatch):
    from src.core.email_utils import check_email_config

    _production(monkeypatch)
    monkeypatch.setattr(settings, "require_email_verification", True)
    check_email_config()  # dummy SMTP values from conftest


def test_production_without_smtp_keeps_verification_and_send_raises(monkeypatch, caplog):
    from src.core import email_utils

    _production(monkeypatch)
    _no_smtp(monkeypatch)
    monkeypatch.setattr(settings, "require_email_verification", True)
    assert email_utils.verification_enforced() is True  # never silently disabled
    token = "tok" + uuid.uuid4().hex
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(email_utils.EmailNotConfigured):
            email_utils.send_reset_email("a@example.com", f"http://x/?reset_token={token}")
        with pytest.raises(email_utils.EmailNotConfigured):
            email_utils.send_verification_email("a@example.com", f"http://x/?verify_token={token}")
    assert token not in caplog.text


def test_non_production_without_smtp_keeps_dev_behaviour(monkeypatch):
    from src.core import email_utils

    monkeypatch.setattr(settings, "env", "test")
    _no_smtp(monkeypatch)
    monkeypatch.setattr(settings, "require_email_verification", True)
    assert email_utils.verification_enforced() is False


def test_production_without_smtp_email_routes_return_503(client, make_user, monkeypatch):
    from src.core.users import get_user_by_email

    existing = make_user("prod")
    _production(monkeypatch)
    _no_smtp(monkeypatch)
    monkeypatch.setattr(settings, "require_email_verification", True)

    unknown = f"nobody_{uuid.uuid4().hex[:10]}@example.com"
    known = client.post(f"{AUTH}/forgot-password", json={"email": existing.email})
    other = client.post(f"{AUTH}/forgot-password", json={"email": unknown})
    # No false "sent" message, and the same answer whether or not the account exists.
    assert known.status_code == other.status_code == 503
    assert known.json() == other.json()
    assert "sent" not in known.text

    resend = client.post(f"{AUTH}/resend-verification", json={"email": existing.email})
    assert resend.status_code == 503

    new_email = f"prodreg_{uuid.uuid4().hex[:10]}@example.com"
    reg = client.post(f"{AUTH}/register", json={"email": new_email, "password": "secret123"})
    assert reg.status_code == 503 and "access_token" not in reg.text
    from src.core.db import SessionLocal

    with SessionLocal() as db:
        assert get_user_by_email(db, new_email) is None  # no unverifiable account created

    # Login for an existing verified account is unaffected (no auth bypass, no lockout).
    ok = client.post(f"{AUTH}/login", data={"username": existing.email, "password": "secret123"})
    assert ok.status_code == 200
