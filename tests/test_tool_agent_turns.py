"""Regression tests: per-question tool budget and citations on a checkpointed thread.

The production Mentor runs the tool agent under a checkpointed supervisor, so
``state['messages']`` holds every earlier turn of the conversation. Two bugs
followed from scanning that whole list:

* the tool-round budget counted earlier turns -> after ~3 searches web search
  was silently disabled for the rest of the thread;
* citations were mined from every earlier tool result -> old sources were
  stapled onto new answers.

The LLM is replaced by a deterministic fake, so this runs offline.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

import src.core.llm as llm_mod
import src.graph.tools as tools_mod
from src.graph.tools import current_turn, tool_rounds


class _FakeModel:
    """Calls web_search once per question when tools are bound, then answers."""

    def __init__(self, with_tools: bool, log: list):
        self.with_tools = with_tools
        self.log = log

    def invoke(self, messages):
        self.log.append(self.with_tools)
        last = messages[-1]
        if self.with_tools and last.type == "human":
            return AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": last.content}, "id": "c1"}],
            )
        return AIMessage(content=f"answer(tools={self.with_tools})")


@pytest.fixture
def agent(monkeypatch):
    log: list = []
    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _FakeModel(False, log))
    monkeypatch.setattr(
        tools_mod, "bind_tools_with_fallback", lambda llm=None, tools=None: _FakeModel(True, log)
    )
    monkeypatch.setitem(
        tools_mod.TOOL_FUNCTIONS,
        "web_search",
        lambda query: f"- hit (Source: https://example.org/{query.replace(' ', '-')})",
    )
    app = tools_mod.build_tool_agent(checkpointer=InMemorySaver(), tools=[])
    return app, log


def _ask(app, question, thread="t1"):
    return app.invoke({"question": question}, {"configurable": {"thread_id": thread}})


def test_web_search_stays_available_on_every_turn(agent):
    app, log = agent
    for i in range(6):
        log.clear()
        result = _ask(app, f"latest news {i}")
        # Each question: one tool-enabled call that searches, then the answer.
        assert log[0] is True, f"turn {i}: tools were not offered"
        assert f"https://example.org/latest-news-{i}" in result["answer"]


def test_answers_cite_only_current_turn_sources(agent):
    app, _ = agent
    for i in range(4):
        result = _ask(app, f"topic {i}")
        assert result["citations"] == [f"web — https://example.org/topic-{i}"]
        for j in range(i):
            assert f"topic-{j}" not in result["answer"]


def test_tool_free_answer_after_search_turn_has_no_stale_sources(monkeypatch):
    """A search turn followed by a tool-free turn: the second answer cites nothing."""

    class _NewsOnly:
        def __init__(self, with_tools):
            self.with_tools = with_tools

        def invoke(self, messages):
            last = messages[-1]
            if self.with_tools and last.type == "human" and "news" in last.content:
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "web_search", "args": {"query": "news"}, "id": "n"}],
                )
            return AIMessage(content="plain answer")

    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _NewsOnly(False))
    monkeypatch.setattr(
        tools_mod, "bind_tools_with_fallback", lambda llm=None, tools=None: _NewsOnly(True)
    )
    monkeypatch.setitem(
        tools_mod.TOOL_FUNCTIONS, "web_search", lambda query: "- x (Source: https://old.example)"
    )
    app = tools_mod.build_tool_agent(checkpointer=InMemorySaver(), tools=[])

    first = _ask(app, "latest news", thread="t2")
    assert "https://old.example" in first["answer"]
    second = _ask(app, "motivate me", thread="t2")
    assert second["answer"] == "plain answer"
    assert second["citations"] == []


def test_tool_loop_cap_still_applies_within_one_question(monkeypatch):
    """A model that keeps calling tools is stopped at the per-question cap."""
    calls = {"tool": 0}

    class _Looper:
        def __init__(self, with_tools):
            self.with_tools = with_tools

        def invoke(self, messages):
            if self.with_tools:
                calls["tool"] += 1
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "x"}],
                )
            return AIMessage(content="forced final answer")

    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _Looper(False))
    monkeypatch.setattr(
        tools_mod, "bind_tools_with_fallback", lambda llm=None, tools=None: _Looper(True)
    )
    monkeypatch.setitem(tools_mod.TOOL_FUNCTIONS, "web_search", lambda query: "nothing")
    app = tools_mod.build_tool_agent(checkpointer=InMemorySaver(), tools=[], max_tool_loops=2)
    cfg = {"configurable": {"thread_id": "loop"}}
    for _ in range(3):  # cap resets for every new question
        calls["tool"] = 0
        result = app.invoke({"question": "keep searching"}, cfg)
        assert calls["tool"] == 2
        assert result["answer"] == "forced final answer"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _ai_call():
    return AIMessage(content="", tool_calls=[{"name": "web_search", "args": {}, "id": "a"}])


def test_current_turn_starts_after_latest_human():
    old = [HumanMessage(content="q1"), _ai_call(), ToolMessage(content="r", tool_call_id="a")]
    new = [HumanMessage(content="q2"), _ai_call()]
    turn = current_turn(old + [AIMessage(content="a1")] + new)
    assert turn == new[1:]


def test_current_turn_without_human_is_whole_list():
    msgs = [_ai_call()]
    assert current_turn(msgs) == msgs
    assert current_turn(None) == []


def test_tool_rounds_ignores_earlier_turns():
    history = [HumanMessage(content="q1"), _ai_call(), _ai_call(), _ai_call()]
    history += [AIMessage(content="a1"), HumanMessage(content="q2")]
    assert tool_rounds(history) == 0
    assert tool_rounds(history + [_ai_call()]) == 1


# --------------------------------------------------------------------------- #
# Structured web sources (artifacts) through the real agent loop
# --------------------------------------------------------------------------- #
def _source(url, title, published=""):
    return {
        "title": title,
        "url": url,
        "publisher": "",
        "published": published,
        "snippet": "s",
        "provider": "fake",
        "official": False,
    }


PIB = "https://pib.gov.in/PressReleasePage.aspx?PRID=7"
HINDU = "https://www.thehindu.com/news/story"
IE = "https://indianexpress.com/article/x"


class _TwoSearches:
    """Searches twice in one question, then answers citing the given numbers."""

    def __init__(self, with_tools, answer, seen):
        self.with_tools = with_tools
        self.answer = answer
        self.seen = seen

    def invoke(self, messages):
        rounds = sum(1 for m in tools_mod.current_turn(messages) if getattr(m, "tool_calls", None))
        self.seen.append([m.content for m in messages if m.type == "tool"])
        if self.with_tools and rounds < 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "web_search", "args": {"query": f"q{rounds}"}, "id": f"s{rounds}"}
                ],
            )
        return AIMessage(content=self.answer)


def _structured_agent(monkeypatch, answer, checkpointer=None):
    seen: list = []
    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _TwoSearches(False, answer, seen))
    monkeypatch.setattr(
        tools_mod,
        "bind_tools_with_fallback",
        lambda llm=None, tools=None: _TwoSearches(True, answer, seen),
    )
    pages = {
        "q0": [_source(PIB, "Release", "2026-10-28"), _source(HINDU, "Story")],
        "q1": [_source(HINDU + "/", "Story again"), _source(IE, "Explainer")],
    }
    monkeypatch.setitem(
        tools_mod.TOOL_FUNCTIONS,
        "web_search",
        lambda query: ("raw text (ignored)", {"sources": pages[query]}),
    )
    app = tools_mod.build_tool_agent(checkpointer=checkpointer or InMemorySaver(), tools=[])
    return app, seen


def test_numbers_are_stable_across_two_searches_in_one_question(monkeypatch):
    app, seen = _structured_agent(monkeypatch, "Notified [1]; analysed [2] and [3].")
    result = _ask(app, "explain the new scheme", thread="s1")
    tool_texts = seen[-1]
    assert "[1] Release" in tool_texts[0] and "[2] Story" in tool_texts[0]
    # second search: repeated Hindu URL keeps [2], the new page gets [3]
    assert "[2] Story again" in tool_texts[1] and "[3] Explainer" in tool_texts[1]
    answer = result["answer"]
    assert f"[Release](<{PIB}>)" in answer and "28 Oct 2026" in answer
    assert "**Official sources**" in answer and "**Secondary sources**" in answer
    assert result["citations"][:3] == [
        f"web [1] — {PIB}",
        f"web [2] — {HINDU}",
        f"web [3] — {IE}",
    ]


def test_answer_lists_only_cited_sources(monkeypatch):
    app, _ = _structured_agent(monkeypatch, "Only this matters [3].")
    answer = _ask(app, "q", thread="s2")["answer"]
    assert "**[3]**" in answer
    assert "**[1]**" not in answer and "**[2]**" not in answer


def test_uncited_answer_lists_retrieved_sources_separately(monkeypatch):
    app, _ = _structured_agent(monkeypatch, "An answer without references.")
    answer = _ask(app, "q", thread="s3")["answer"]
    assert "**Retrieved sources (not cited inline)**" in answer
    assert "**Official sources**" not in answer
    for n in (1, 2, 3):
        assert f"**[{n}]**" in answer


def test_artifacts_survive_checkpoint_and_next_question_starts_fresh(monkeypatch):
    saver = InMemorySaver()
    app, _ = _structured_agent(monkeypatch, "Cited [1].", checkpointer=saver)
    _ask(app, "first question", thread="s4")
    state = app.get_state({"configurable": {"thread_id": "s4"}}).values
    tool_msgs = [m for m in state["messages"] if m.type == "tool"]
    assert tool_msgs and all(m.artifact["sources"] for m in tool_msgs)
    assert tool_msgs[0].artifact["sources"][0]["n"] == 1
    # Next question: numbering restarts at [1] and old sources are not reused.
    second = _ask(app, "second question", thread="s4")
    assert second["citations"][0] == f"web [1] — {PIB}"
    assert second["answer"].count("**[1]**") == 1
