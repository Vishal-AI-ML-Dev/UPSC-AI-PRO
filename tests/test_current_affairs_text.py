"""Current Affairs model-text handling, fully mocked (no provider or news fetch).

Gemini may stream ``AIMessageChunk.content`` as a list of blocks. The daily,
editorial and monthly generators must yield plain text only (never a list,
which breaks ``StreamingResponse``) and show their failure message when the
model produced no text at all.
"""

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

import src.agents.current_affairs.graph as ca
import src.agents.current_affairs.ingest as ingest
import src.agents.current_affairs.monthly_ingest as monthly_ingest

API = "/api/v1"
THOUGHT = "PRIVATE-THINKING-TEXT"
LIST_STREAM = [
    [{"type": "text", "text": "Topic one. "}],
    [{"type": "thinking", "thinking": THOUGHT}],
    [{"type": "text", "text": "Topic two."}],
]
EXPECTED = "Topic one. Topic two."

GENERATORS = {
    # Daily generates only for today and only with headlines (see test_current_affairs_dates).
    "daily": (lambda: ca.get_daily_ca(ca.today_label()), "Could not generate current affairs"),
    "editorial": (
        lambda: ca.get_editorial("Electoral Reforms in India"),
        "Could not generate editorial",
    ),
    "monthly": (
        lambda: ca.get_monthly_summary("January", "2026"),
        "Could not generate monthly digest",
    ),
}


@pytest.fixture
def fake_model(monkeypatch):
    """Patch the model to stream the given chunk contents; no news fetch."""
    monkeypatch.setattr(ingest, "fetch_headlines", lambda **kw: "PIB: headline - snippet")
    monkeypatch.setattr(monthly_ingest, "fetch_month_news", lambda month, year: ("Item 1", 1))

    def use(contents):
        def stream(inputs):
            for _ in inputs:
                pass
            for content in contents:
                yield AIMessageChunk(content=content)

        monkeypatch.setattr(ca, "get_llm", lambda: RunnableGenerator(stream))

    return use


@pytest.mark.parametrize("name", list(GENERATORS))
def test_list_content_streams_plain_text(fake_model, name):
    fake_model(LIST_STREAM)
    chunks = list(GENERATORS[name][0]())
    assert all(isinstance(c, str) and c for c in chunks)
    body = "".join(chunks)
    assert body.endswith(EXPECTED)
    assert THOUGHT not in body
    assert "Could not generate" not in body


@pytest.mark.parametrize("name", list(GENERATORS))
def test_string_content_unchanged(fake_model, name):
    fake_model(["Plain ", "string."])
    body = "".join(GENERATORS[name][0]())
    assert body.endswith("Plain string.")


@pytest.mark.parametrize("name", list(GENERATORS))
@pytest.mark.parametrize(
    "contents",
    [[], [""], [[]], [[{"type": "thinking", "thinking": THOUGHT}]]],
    ids=["no-chunks", "empty-string", "empty-list", "thinking-only"],
)
def test_no_text_shows_failure_message(fake_model, name, contents):
    fake_model(contents)
    chunks = list(GENERATORS[name][0]())
    assert all(isinstance(c, str) for c in chunks)
    body = "".join(chunks)
    assert GENERATORS[name][1] in body
    assert THOUGHT not in body


def test_daily_endpoint_streams_text_after_banner(client, make_user, fake_model):
    fake_model(LIST_STREAM)
    user = make_user("ca")
    r = client.post(
        f"{API}/current-affairs/daily", json={"date": ca.today_label()}, headers=user.headers
    )
    assert r.status_code == 200
    assert r.text.endswith(EXPECTED)
    assert THOUGHT not in r.text
