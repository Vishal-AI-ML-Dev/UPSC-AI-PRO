"""Phase 03 — the mentor sends a bounded history to the model (spec R5/R6, AC4).

On the default (one stable per-user) thread the checkpoint grows forever. The
model must only see the system prompt, the last ``mentor_history_max_turns``
earlier turns (question + final answer only) and the whole current turn. The
checkpoint itself keeps every message. Offline: the LLM is a recording fake.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

import src.core.llm as llm_mod
import src.graph.tools as tools_mod
from src.core.config import settings
from src.graph.tools import bound_history


def _turn(i: int, *, tools: bool = True) -> list:
    msgs = [HumanMessage(content=f"q{i}")]
    if tools:
        msgs += [
            AIMessage(content="", tool_calls=[{"name": "web_search", "args": {}, "id": f"c{i}"}]),
            ToolMessage(content=f"result{i}", tool_call_id=f"c{i}", name="web_search"),
        ]
    msgs.append(AIMessage(content=f"a{i}"))
    return msgs


def _contents(msgs) -> list:
    return [(m.type, m.content) for m in msgs]


# --------------------------------------------------------------------- unit ---
def test_keeps_last_n_earlier_turns_as_question_and_answer():
    history = [m for i in range(10) for m in _turn(i)] + [HumanMessage(content="now")]
    out = bound_history(history, 2)
    assert _contents(out) == [
        ("human", "q8"),
        ("ai", "a8"),
        ("human", "q9"),
        ("ai", "a9"),
        ("human", "now"),
    ]


def test_current_turn_is_kept_intact_including_tool_messages():
    current = _turn(99)[:-1]  # question, tool call, tool result (answer pending)
    history = [m for i in range(5) for m in _turn(i)] + current
    out = bound_history(history, 1)
    assert out[-3:] == current
    assert _contents(out[:2]) == [("human", "q4"), ("ai", "a4")]


def test_no_tool_messages_or_tool_calls_from_earlier_turns():
    history = [m for i in range(4) for m in _turn(i)] + [HumanMessage(content="now")]
    out = bound_history(history, 10)
    assert all(m.type != "tool" for m in out)
    assert all(not getattr(m, "tool_calls", None) for m in out)
    assert len(out) == 4 * 2 + 1


def test_zero_keeps_only_the_current_turn():
    history = [m for i in range(3) for m in _turn(i)] + [HumanMessage(content="now")]
    assert _contents(bound_history(history, 0)) == [("human", "now")]
    assert _contents(bound_history(history, -1)) == [("human", "now")]


def test_never_starts_with_a_non_human_message():
    # Other supervisor routes append answers without a human message.
    history = [AIMessage(content="planner answer"), *_turn(1), HumanMessage(content="now")]
    out = bound_history(history, 5)
    assert out[0].type == "human"
    assert _contents(out) == [("human", "q1"), ("ai", "a1"), ("human", "now")]


def test_earlier_turn_keeps_only_its_own_answer():
    # Checkpoints written before Phase 05 hold other-route answers without their
    # question. Only ONE answer per earlier turn may reach the model, and it is the
    # turn's own (first) answer, so the question stays paired with its reply.
    # (Phase 03 kept the last answer; changed in Phase 05 review S1, user decision.)
    history = [*_turn(1), AIMessage(content="planner answer"), HumanMessage(content="now")]
    out = bound_history(history, 5)
    assert _contents(out) == [("human", "q1"), ("ai", "a1"), ("human", "now")]


def test_many_other_route_answers_do_not_bypass_the_bound():
    others = [AIMessage(content=f"other {i}") for i in range(100)]
    history = [*_turn(1), *others, HumanMessage(content="now")]
    out = bound_history(history, 6)
    assert _contents(out) == [("human", "q1"), ("ai", "a1"), ("human", "now")]


def _route_turn(q: str, a: str) -> list:
    """A planner / evaluator / CA turn as stored since Phase 05: question + answer."""
    return [HumanMessage(content=q), AIMessage(content=a)]


def test_mixed_routes_keep_coherent_pairs():
    history = [
        *_turn(1),
        *_route_turn("plan please", "PLAN"),
        *_turn(2),
        *_route_turn("evaluate this", "EVAL"),
        HumanMessage(content="now"),
    ]
    assert _contents(bound_history(history, 6)) == [
        ("human", "q1"),
        ("ai", "a1"),
        ("human", "plan please"),
        ("ai", "PLAN"),
        ("human", "q2"),
        ("ai", "a2"),
        ("human", "evaluate this"),
        ("ai", "EVAL"),
        ("human", "now"),
    ]


def test_multiple_unanswered_questions_between_mixed_routes():
    # Questions that never got an answer (e.g. timeouts), around route turns.
    history = [
        *_route_turn("plan please", "PLAN"),
        HumanMessage(content="lost 1"),
        *_turn(1)[:-1],  # q1 stopped after its tool round: no answer
        HumanMessage(content="lost 2"),
        *_route_turn("evaluate this", "EVAL"),
        HumanMessage(content="now"),
    ]
    out = bound_history(history, 6)
    assert _contents(out) == [
        ("human", "plan please"),
        ("ai", "PLAN"),
        ("human", "lost 1"),
        ("human", "q1"),
        ("human", "lost 2"),
        ("human", "evaluate this"),
        ("ai", "EVAL"),
        ("human", "now"),
    ]
    assert all(m.type != "tool" and not getattr(m, "tool_calls", None) for m in out)
    # Unanswered questions count as turns under the six-turn policy.
    assert _contents(bound_history(history, 2)) == [
        ("human", "lost 2"),
        ("human", "evaluate this"),
        ("ai", "EVAL"),
        ("human", "now"),
    ]


def test_unanswered_earlier_questions_are_kept_alone_and_count_as_turns():
    history = [
        *_turn(1),
        HumanMessage(content="unanswered a"),
        HumanMessage(content="unanswered b"),
        *_turn(2),
        HumanMessage(content="now"),
    ]
    assert _contents(bound_history(history, 3)) == [
        ("human", "unanswered a"),
        ("human", "unanswered b"),
        ("human", "q2"),
        ("ai", "a2"),
        ("human", "now"),
    ]
    assert _contents(bound_history(history, 1)) == [("human", "q2"), ("ai", "a2"), ("human", "now")]


def test_interrupted_earlier_turn_leaves_no_orphan_tool_call_or_result():
    # A turn that stopped after the tool call / tool result, with no final answer.
    interrupted = _turn(1)[:-1]
    history = [*interrupted, *_turn(2), HumanMessage(content="now")]
    out = bound_history(history, 5)
    assert _contents(out) == [("human", "q1"), ("human", "q2"), ("ai", "a2"), ("human", "now")]
    assert all(m.type != "tool" and not getattr(m, "tool_calls", None) for m in out)


def test_ai_message_with_text_and_tool_calls_is_not_an_answer():
    mixed = AIMessage(
        content="let me check", tool_calls=[{"name": "web_search", "args": {}, "id": "m"}]
    )
    history = [HumanMessage(content="q1"), mixed, HumanMessage(content="now")]
    assert _contents(bound_history(history, 5)) == [("human", "q1"), ("human", "now")]


def test_empty_and_humanless_histories_are_returned_unchanged():
    assert bound_history([], 3) == []
    lone = [AIMessage(content="x")]
    assert bound_history(lone, 3) == lone


def test_does_not_mutate_input():
    history = [m for i in range(3) for m in _turn(i)] + [HumanMessage(content="now")]
    snapshot = list(history)
    bound_history(history, 1)
    assert history == snapshot


def test_default_setting_is_six():
    from src.core.config import Settings

    assert Settings.model_fields["mentor_history_max_turns"].default == 6


# -------------------------------------------------------------- integration ---
class _RecordingModel:
    """Searches once per question when tools are bound, then answers."""

    def __init__(self, with_tools: bool, calls: list):
        self.with_tools = with_tools
        self.calls = calls

    def invoke(self, messages):
        self.calls.append(list(messages))
        last = messages[-1]
        if self.with_tools and last.type == "human":
            return AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": last.content}, "id": "t"}],
            )
        return AIMessage(content=f"answer to {self._question(messages)}")

    @staticmethod
    def _question(messages):
        return next(m.content for m in reversed(messages) if m.type == "human")


@pytest.fixture
def agent(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _RecordingModel(False, calls))
    monkeypatch.setattr(
        tools_mod,
        "bind_tools_with_fallback",
        lambda llm=None, tools=None: _RecordingModel(True, calls),
    )
    monkeypatch.setitem(tools_mod.TOOL_FUNCTIONS, "web_search", lambda query: f"- hit for {query}")
    app = tools_mod.build_tool_agent(checkpointer=InMemorySaver(), tools=[])
    return app, calls


def test_agent_sends_bounded_history_but_checkpoint_keeps_everything(agent, monkeypatch):
    app, calls = agent
    monkeypatch.setattr(settings, "mentor_history_max_turns", 2)
    config = {"configurable": {"thread_id": "long-thread"}}
    for i in range(20):
        app.invoke({"question": f"question {i}"}, config)

    calls.clear()
    app.invoke({"question": "final question"}, config)

    first, second = calls  # tool-call round, then the answer round
    assert isinstance(first[0], SystemMessage)
    assert _contents(first[1:]) == [
        ("human", "question 18"),
        ("ai", "answer to question 18"),
        ("human", "question 19"),
        ("ai", "answer to question 19"),
        ("human", "final question"),
    ]
    # The current turn's tool call and result reach the model intact.
    assert [m.type for m in second[-3:]] == ["human", "ai", "tool"]
    assert second[-2].tool_calls and second[-1].tool_call_id == second[-2].tool_calls[0]["id"]
    assert sum(m.type == "tool" for m in second) == 1

    stored = app.get_state(config).values["messages"]
    assert len(stored) == 21 * 4  # human, tool call, tool result, answer per question
    assert stored[-1].content.startswith("answer to final question")
