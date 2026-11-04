"""Phase 08 Slice A (AC-A2): every stream notice starts with STREAM_NOTICE_PREFIX.

The Mentor UI (`MentorChat.persist`) must not save an error / stop notice as an
assistant answer. It recognises notices by the shared prefix ``"\\n\\n⚠️ "``
(exported as ``STREAM_NOTICE_PREFIX`` from ``frontend/src/lib/api.ts``). These
tests pin that every notice the backend can put into the Mentor stream, and
every notice ``streamAgent`` emits itself, uses exactly that prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api.stream_guard import STOP_NOTICES
from src.core.llm import LLMTimeoutError

PREFIX = "\n\n⚠️ "  # "\n\n⚠️ " — U+26A0 + U+FE0F, then a space
CHAT = "/api/v1/agent/chat/stream"
QUESTION = "Explain the doctrine of basic structure in the Indian Constitution."
API_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api.ts"


class _NoCache:
    def get(self, **_kw):
        return None

    def set(self, **_kw):
        raise AssertionError("a notice must never be cached as an answer")


class _FailingGraph:
    def __init__(self, exc: BaseException):
        self.exc = exc

    def stream(self, *_a, **_kw):
        raise self.exc


@pytest.fixture
def no_cache(monkeypatch):
    from src.core import response_cache as rc

    monkeypatch.setattr(rc, "_cache_singleton", _NoCache())


@pytest.mark.parametrize("key", sorted(STOP_NOTICES))
def test_stop_notices_start_with_prefix(key):
    assert STOP_NOTICES[key].startswith(PREFIX)
    assert STOP_NOTICES[key][len(PREFIX) :].strip()


@pytest.mark.parametrize(
    "exc, expected",
    [
        (LLMTimeoutError("deadline"), "did not respond in time"),
        (RuntimeError("provider exploded"), "the agent hit an error"),
    ],
    ids=["timeout", "error"],
)
def test_chat_stream_error_notices_start_with_prefix(
    client, make_user, monkeypatch, no_cache, exc, expected
):
    user = make_user("notice")
    monkeypatch.setattr(client.app.state, "agent_graph", _FailingGraph(exc))
    r = client.post(CHAT, json={"question": QUESTION}, headers=user.headers)
    assert r.status_code == 200
    assert r.text.startswith(PREFIX)
    assert expected in r.text
    assert "provider exploded" not in r.text


def _stream_agent_source() -> str:
    src = API_TS.read_text(encoding="utf-8")
    start = src.index("export async function streamAgent(")
    end = src.index("\n}\n", start)
    return src[start:end]


def test_frontend_prefix_constant_matches_backend():
    src = API_TS.read_text(encoding="utf-8")
    m = re.search(r'export const STREAM_NOTICE_PREFIX = "(.*?)"\n', src)
    assert m, "STREAM_NOTICE_PREFIX must be exported from lib/api.ts"
    assert m.group(1).replace("\\n", "\n") == PREFIX


def test_stream_agent_builds_every_notice_with_prefix():
    body = _stream_agent_source()
    emits = re.findall(r"emit\(\s*([^)]*)", body)
    assert len(emits) >= 5  # config, connection, refresh-denied, 401, HTTP error
    for arg in emits:
        if arg.strip() == "piece":  # upstream stream text, passed through unchanged
            continue
        assert arg.strip().startswith("STREAM_NOTICE_PREFIX"), arg
    assert "⚠" not in body, "notices must use STREAM_NOTICE_PREFIX, not a literal"


# --------------------------------------------------------------------------- #
# Amendment A-1 (S1): specialist failure fallbacks carry the prefix
# --------------------------------------------------------------------------- #
import uuid  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

import src.agents.current_affairs.graph as ca  # noqa: E402
import src.agents.current_affairs.ingest as ca_ingest  # noqa: E402
import src.agents.current_affairs.monthly_ingest as ca_monthly  # noqa: E402
import src.agents.evaluator.graph as evaluator  # noqa: E402
import src.agents.planner.graph as planner  # noqa: E402
import src.graph.supervisor as supervisor  # noqa: E402
from src.core.stream_notices import STREAM_NOTICE_PREFIX, stream_notice  # noqa: E402

E_QUESTION = "Discuss the role of the Gram Sabha in rural local governance."
E_ANSWER = " ".join(["The Gram Sabha includes all adult voters of the village."] * 8)

# name -> (module whose get_llm is patched, generator factory, exact failure message)
SPECIALISTS = {
    "plan": (
        planner,
        lambda: planner.generate_plan("UPSC 2027", "6", "", "", "1"),
        "Plan generation failed. Please try again.",
    ),
    "evaluate": (
        evaluator,
        lambda: evaluator.evaluate_answer(E_QUESTION, E_ANSWER),
        "Evaluation failed. Please try again.",
    ),
    "mains": (
        evaluator,
        lambda: evaluator.evaluate_mains(E_QUESTION, E_ANSWER),
        "Evaluation failed. Please try again.",
    ),
    "model_answer": (
        evaluator,
        lambda: evaluator.get_model_answer(E_QUESTION),
        "Could not generate model answer. Please try again.",
    ),
    "ca_daily": (
        ca,
        lambda: ca.get_daily_ca(ca.today_label()),
        "Could not generate current affairs. Please retry.",
    ),
    "ca_editorial": (
        ca,
        lambda: ca.get_editorial("Electoral Reforms in India"),
        "Could not generate editorial. Please retry.",
    ),
    "ca_monthly": (
        ca,
        lambda: ca.get_monthly_summary("January", "2026"),
        "Could not generate monthly digest. Please retry.",
    ),
}


def _llm(kind: str):
    def _f(_prompt):
        if kind == "error":
            raise RuntimeError("provider exploded")
        if kind == "timeout":
            raise LLMTimeoutError("deadline")
        if kind == "empty":
            return AIMessage(content="")
        return AIMessage(content="## Score: 6/10\n\nA normal, legitimate answer.")

    return lambda: RunnableLambda(_f)


@pytest.fixture
def offline_specialists(monkeypatch):
    monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: "")
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: False)
    monkeypatch.setattr(ca_ingest, "fetch_headlines", lambda **kw: "PIB: headline - snippet")
    monkeypatch.setattr(ca_monthly, "fetch_month_news", lambda month, year: ("Item 1", 1))

    def run(name: str, kind: str) -> str:
        module, factory, _ = SPECIALISTS[name]
        monkeypatch.setattr(module, "get_llm", _llm(kind))
        return list(factory())

    return run


def test_backend_prefix_matches_contract():
    assert STREAM_NOTICE_PREFIX == PREFIX
    assert stream_notice("x") == PREFIX + "x"


@pytest.mark.parametrize("kind", ["error", "timeout", "empty"])
@pytest.mark.parametrize("name", list(SPECIALISTS))
def test_specialist_failure_ends_with_prefixed_notice(offline_specialists, name, kind):
    chunks = offline_specialists(name, kind)
    # The failure chunk itself is the notice, whatever was streamed before it.
    assert chunks[-1] == PREFIX + SPECIALISTS[name][2]
    assert all(PREFIX not in c for c in chunks[:-1])  # headers / disclaimers are not notices
    assert "provider exploded" not in "".join(chunks)


@pytest.mark.parametrize("name", list(SPECIALISTS))
def test_specialist_success_has_no_prefix(offline_specialists, name):
    body = "".join(offline_specialists(name, "ok"))
    assert "A normal, legitimate answer." in body
    assert PREFIX not in body


@pytest.mark.parametrize(
    "factory",
    [
        lambda: evaluator.evaluate_answer(E_QUESTION, "far too short " * 4),
        lambda: evaluator.evaluate_answer("", E_ANSWER),
        lambda: evaluator.evaluate_mains(E_QUESTION, "too short " * 5),
        lambda: ca.get_daily_ca("01 January 2001"),
    ],
    ids=["eval-too-short", "eval-no-question", "mains-too-short", "ca-other-date"],
)
def test_legitimate_warnings_keep_their_text(factory):
    body = "".join(factory())
    assert "⚠" in body
    assert PREFIX not in body


def test_degraded_news_answers_are_not_notices(monkeypatch):
    monkeypatch.setattr(ca_ingest, "fetch_headlines", lambda **kw: "")
    monkeypatch.setattr(ca_monthly, "fetch_month_news", lambda month, year: ("", 0))
    for body in (
        "".join(ca.get_daily_ca(ca.today_label())),
        "".join(ca.get_monthly_summary("January", "2001")),
    ):
        assert body.startswith("> ⚠")
        assert PREFIX not in body


class _IgnoreCache:
    def get(self, **_kw):
        return None

    def set(self, **_kw):
        pass  # failure-text caching is a separate backlog item (MED)


@pytest.mark.parametrize(
    "route, question",
    [
        ("planner", "Make a study plan for UPSC 2027 with 6 hours a day"),
        ("evaluator", f"Evaluate\nQuestion: {E_QUESTION}\nAnswer: {E_ANSWER}"),
        ("current_affairs", "Give me an editorial analysis on electoral reforms"),
    ],
)
def test_mentor_chat_specialist_failure_is_a_notice(
    client, make_user, monkeypatch, offline_specialists, route, question
):
    """Through the real /agent/chat/stream: the streamed text contains the prefix,
    so MentorChat (`persistExchange`) saves neither the question nor the notice."""
    from src.core import response_cache as rc

    class _Router:
        def invoke(self, *a, **k):
            return supervisor.RouteDecision(route=route)

    monkeypatch.setattr(rc, "_cache_singleton", _IgnoreCache())
    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: _Router())
    for module in (planner, evaluator, ca):
        monkeypatch.setattr(module, "get_llm", _llm("error"))
    user = make_user("notice-" + route)
    r = client.post(
        CHAT, json={"question": f"{question} ({uuid.uuid4().hex[:6]})"}, headers=user.headers
    )
    assert r.status_code == 200
    assert PREFIX in r.text
    assert "provider exploded" not in r.text
    assert (
        client.get("/api/v1/history/conversations", headers=user.headers).json()["conversations"]
        == []
    )
