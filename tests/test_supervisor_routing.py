"""Phase 05: every Mentor chat message is routed on its own (offline, mocked).

Regression for the "sticky route" bug: the supervisor treated ``state['route']``
as a forced route, and the checkpointer saved each turn's route, so on a
conversation thread every message after the first went to the FIRST message's
specialist and the router LLM ran once per thread. The supervisor also built an
unreachable ``rag`` route (no API sets ``persist_key``); Phase 05 removed it.

No provider, web or vector-store call is made: the router and the specialists
are stubs, or the specialists' feature functions are patched.
"""

import re
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

import src.agents.evaluator.graph as evaluator
import src.core.llm as llm_mod
import src.graph.agent_subgraphs as subgraphs
import src.graph.supervisor as supervisor
import src.graph.tools as tools_mod
from src.core.config import settings
from src.graph.state import AgentState

SPECIALISTS = ("mentor", "planner", "evaluator", "current_affairs")

PLANNER_MSG = "Make me a UPSC 2027 study plan, 6 hours a day, 1st attempt"
MENTOR_MSG = "Explain the difference between Fundamental Rights and DPSP"
CA_MSG = "Give me today's current affairs"


class ScriptedRouter:
    """Router stand-in: returns the scripted routes in order, records each call."""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def invoke(self, messages, *args, **kwargs):
        self.calls.append(dict(messages).get("human"))
        return supervisor.RouteDecision(route=self.routes.pop(0))


@pytest.fixture
def seen():
    """What each stub specialist received, in call order."""
    return []


@pytest.fixture
def build(monkeypatch, seen):
    """Build a checkpointed supervisor with stub specialists and a scripted router."""

    def node(name):
        def run(state):
            seen.append(
                {
                    "node": name,
                    "messages": [m.content for m in state.get("messages") or []],
                    "student_context": state.get("student_context"),
                }
            )
            return {"answer": name, "messages": [AIMessage(content=f"{name}-answer")]}

        return run

    monkeypatch.setattr(supervisor, "build_mentor_graph", lambda: node("mentor"))
    monkeypatch.setattr(supervisor, "build_planner_subgraph", lambda: node("planner"))
    monkeypatch.setattr(supervisor, "build_evaluator_subgraph", lambda: node("evaluator"))
    monkeypatch.setattr(
        supervisor, "build_current_affairs_subgraph", lambda: node("current_affairs")
    )

    def _build(routes):
        router = ScriptedRouter(routes)
        monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: router)
        return supervisor.build_supervisor(checkpointer=InMemorySaver()), router

    return _build


def _thread():
    return {"configurable": {"thread_id": f"t-{uuid.uuid4().hex}"}}


# ----------------------------- AC1 / AC9 ------------------------------------ #
def test_conversation_planner_then_mentor_then_current_affairs(build):
    graph, router = build(["planner", "mentor", "current_affairs"])
    cfg = _thread()
    answers = [
        graph.invoke({"question": q, "student_context": {}}, cfg)["answer"]
        for q in (PLANNER_MSG, MENTOR_MSG, CA_MSG)
    ]
    assert answers == ["planner", "mentor", "current_affairs"]
    # The router ran once per message, each time on that message's own text.
    assert router.calls == [PLANNER_MSG, MENTOR_MSG, CA_MSG]


def test_router_runs_for_every_message_of_a_long_conversation(build):
    routes = ["mentor", "planner", "mentor", "evaluator", "current_affairs", "mentor"]
    graph, router = build(routes)
    cfg = _thread()
    answers = [
        graph.invoke({"question": f"message {i}"}, cfg)["answer"] for i in range(len(routes))
    ]
    assert answers == ["mentor", "planner", "mentor", "evaluator", "current_affairs", "mentor"]
    assert len(router.calls) == len(routes)


# ----------------------------- AC3 ------------------------------------------ #
def test_saved_route_never_forces_the_next_message(build):
    graph, router = build(["planner", "mentor"])
    cfg = _thread()
    graph.invoke({"question": PLANNER_MSG}, cfg)
    assert graph.get_state(cfg).values.get("route") == "planner"  # saved by the checkpointer
    out = graph.invoke({"question": MENTOR_MSG}, cfg)
    assert out["answer"] == "mentor"
    assert out["route"] == "mentor"


@pytest.mark.parametrize("stale", ["planner", "evaluator", "current_affairs", "mentor", "rag"])
def test_route_in_the_input_does_not_bypass_the_router(build, stale):
    graph, router = build(["current_affairs"])
    out = graph.invoke({"question": CA_MSG, "route": stale}, _thread())
    assert out["answer"] == "current_affairs"
    assert router.calls == [CA_MSG]


# ----------------------------- AC10 ----------------------------------------- #
def test_history_and_profile_still_reach_the_specialist(build, seen):
    graph, _ = build(["planner", "mentor", "current_affairs"])
    cfg = _thread()
    profile = {"stage": "prelims", "weak_areas": ["economy"]}
    for q in (PLANNER_MSG, MENTOR_MSG, CA_MSG):
        graph.invoke({"question": q, "student_context": profile}, cfg)
    third = seen[-1]
    assert third["node"] == "current_affairs"
    # Earlier answers on the thread are still in the conversation state.
    assert third["messages"][:2] == ["planner-answer", "mentor-answer"]
    assert third["student_context"] == profile


# ----------------------------- AC4 ------------------------------------------ #
def test_supervisor_has_no_rag_route(build):
    graph, _ = build([])
    nodes = set(graph.get_graph().nodes)
    assert "rag" not in nodes
    assert set(SPECIALISTS) <= nodes
    assert "rag" not in supervisor._ROUTES
    with pytest.raises(ValidationError):
        supervisor.RouteDecision(route="rag")
    assert not re.search(r"\brag\b", supervisor._SUP_SYS)


def test_document_question_with_persist_key_is_still_routed_by_the_router(build):
    graph, router = build(["mentor"])
    out = graph.invoke(
        {"question": "explain NCERT class 6 chapter 11", "persist_key": "ncert_x"}, _thread()
    )
    assert out["answer"] == "mentor"
    assert len(router.calls) == 1


# ----------------------------- AC5 ------------------------------------------ #
def test_task_inputs_is_not_part_of_the_state_contract():
    assert "task_inputs" not in AgentState.__annotations__


# ----------------------------- AC6 ------------------------------------------ #
def test_eval_harness_still_builds_the_crag_subgraph():
    import src.eval.llm_eval as llm_eval

    graph = llm_eval.build_rag_subgraph(label="rag")
    assert {"retrieve", "grade", "generate"} <= set(graph.get_graph().nodes)


# ----------------------------- AC2 (real endpoint) -------------------------- #
EVAL_TEXT = "## Score: 6/10\n\n### What You Did Well\n- Clear definition\n"
QUESTION = "Discuss the role of the Gram Sabha in rural local governance."
ANSWER = " ".join(["The Gram Sabha includes all adult voters of the village."] * 8)


def test_chat_endpoint_routes_each_message_of_one_thread(client, make_user, monkeypatch):
    router = ScriptedRouter(["planner", "evaluator", "current_affairs"])
    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: router)

    def fake_plan(**kwargs):
        yield "PLAN-OUT"

    def fake_daily(date):
        yield "CA-OUT"

    monkeypatch.setattr(subgraphs, "generate_plan", fake_plan)
    monkeypatch.setattr(subgraphs, "get_daily_ca", fake_daily)
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=EVAL_TEXT))
    )
    user = make_user("routing")
    tag = uuid.uuid4().hex[:8]  # unique text: never a response-cache hit
    thread = f"conv-{tag}"
    messages = [
        f"{PLANNER_MSG} ({tag})",
        f"Evaluate ({tag})\nQuestion: {QUESTION}\nAnswer: {ANSWER}",
        f"{CA_MSG} ({tag})",
    ]
    texts = []
    for msg in messages:
        r = client.post(
            "/api/v1/agent/chat/stream",
            json={"question": msg, "thread_id": thread},
            headers=user.headers,
        )
        assert r.status_code == 200
        texts.append(r.text)
    assert "PLAN-OUT" in texts[0]
    assert "Score: 6/10" in texts[1] and "PLAN-OUT" not in texts[1]
    assert "CA-OUT" in texts[2] and "PLAN-OUT" not in texts[2]
    assert router.calls == messages


# ------------------- S1 (review): coherent history across specialists ------- #
# Real mentor tool agent (recording fake model) + real planner / evaluator /
# current-affairs specialists (feature functions patched). Each specialist must
# record the student's question once, so the mentor later sees question->answer
# pairs instead of answers without their questions.
class _MentorModel:
    """Searches once per question when tools are bound, then answers."""

    def __init__(self, with_tools, calls):
        self.with_tools = with_tools
        self.calls = calls

    def invoke(self, messages):
        self.calls.append(list(messages))
        last = messages[-1]
        if self.with_tools and last.type == "human":
            return AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "t"}],
            )
        question = next(m.content for m in reversed(messages) if m.type == "human")
        return AIMessage(content=f"mentor answer to {question}")


@pytest.fixture
def mixed(monkeypatch):
    """Real supervisor + real specialists; returns (graph, router, mentor_calls)."""
    calls: list = []
    monkeypatch.setattr(llm_mod, "get_llm_for_tier", lambda tier: _MentorModel(False, calls))
    monkeypatch.setattr(
        tools_mod,
        "bind_tools_with_fallback",
        lambda llm=None, tools=None: _MentorModel(True, calls),
    )
    monkeypatch.setitem(tools_mod.TOOL_FUNCTIONS, "web_search", lambda query: "- a hit")

    def fake_plan(**kwargs):
        yield "PLAN-OUT"

    def fake_daily(date):
        yield "CA-OUT"

    monkeypatch.setattr(subgraphs, "generate_plan", fake_plan)
    monkeypatch.setattr(subgraphs, "get_daily_ca", fake_daily)
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=EVAL_TEXT))
    )
    router = ScriptedRouter([])
    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: router)
    return supervisor.build_supervisor(checkpointer=InMemorySaver()), router, calls


EVAL_MSG = f"Question: {QUESTION}\nAnswer: {ANSWER}"
FOLLOW_UP = "How should I revise this?"
FORM_BUILDERS = (
    "build_planner_subgraph",
    "build_evaluator_subgraph",
    "build_current_affairs_subgraph",
)


def _pairs(messages):
    return [(m.type, m.content) for m in messages]


def _run(graph, router, cfg, turns):
    for route, question in turns:
        router.routes.append(route)
        graph.invoke({"question": question}, cfg)


def _form_question(build_fn):
    return EVAL_MSG if "evaluator" in build_fn else PLANNER_MSG


@pytest.mark.parametrize(
    ("route", "question", "answer"),
    [("planner", PLANNER_MSG, "PLAN-OUT"), ("evaluator", EVAL_MSG, EVAL_TEXT)],
    ids=["planner", "evaluator"],
)
def test_mentor_then_specialist_then_mentor_sees_coherent_pairs(mixed, route, question, answer):
    graph, router, calls = mixed
    cfg = _thread()
    _run(graph, router, cfg, [("mentor", MENTOR_MSG), (route, question)])
    calls.clear()
    _run(graph, router, cfg, [("mentor", FOLLOW_UP)])
    sent = calls[0][1:]  # first mentor model call of turn 3, after the system prompt
    assert _pairs(sent) == [
        ("human", MENTOR_MSG),
        ("ai", f"mentor answer to {MENTOR_MSG}"),
        ("human", question),
        ("ai", answer),
        ("human", FOLLOW_UP),
    ]


def test_each_question_is_stored_exactly_once(mixed):
    graph, router, _ = mixed
    cfg = _thread()
    turns = [
        ("mentor", MENTOR_MSG),
        ("planner", PLANNER_MSG),
        ("evaluator", EVAL_MSG),
        ("current_affairs", CA_MSG),
        ("mentor", FOLLOW_UP),
    ]
    _run(graph, router, cfg, turns)
    stored = graph.get_state(cfg).values["messages"]
    assert [m.content for m in stored if m.type == "human"] == [q for _, q in turns]
    # Every specialist answer directly follows its own question.
    for _, q in turns[1:4]:
        i = next(i for i, m in enumerate(stored) if m.type == "human" and m.content == q)
        assert stored[i + 1].type == "ai" and not stored[i + 1].tool_calls


@pytest.mark.parametrize("build_fn", FORM_BUILDERS)
def test_specialist_records_question_then_answer(mixed, build_fn):
    q = _form_question(build_fn)
    out = getattr(subgraphs, build_fn)().invoke({"question": q})
    assert [m.type for m in out["messages"]] == ["human", "ai"]
    assert out["messages"][0].content == q
    assert out["messages"][1].content == out["answer"]


@pytest.mark.parametrize("build_fn", FORM_BUILDERS)
def test_specialist_does_not_duplicate_a_question_already_in_state(mixed, build_fn):
    # The same question is already the latest, unanswered human message (an
    # earlier attempt stopped after recording it): it must not be added again.
    q = _form_question(build_fn)
    prior = [
        HumanMessage(content="older"),
        AIMessage(content="older answer"),
        HumanMessage(content=q),
    ]
    out = getattr(subgraphs, build_fn)().invoke({"question": q, "messages": prior})
    assert [m.content for m in out["messages"] if m.type == "human"] == ["older", q]
    assert out["messages"][-1].type == "ai"


def test_history_stays_bounded_after_mixed_workflows(mixed, monkeypatch):
    graph, router, calls = mixed
    monkeypatch.setattr(settings, "mentor_history_max_turns", 2)
    cfg = _thread()
    turns = [
        ("mentor", "m1"),
        ("planner", PLANNER_MSG),
        ("evaluator", EVAL_MSG),
        ("current_affairs", CA_MSG),
        ("mentor", "m2"),
        ("planner", PLANNER_MSG + " v2"),
    ]
    _run(graph, router, cfg, turns)
    calls.clear()
    _run(graph, router, cfg, [("mentor", "last")])
    sent = calls[0][1:]
    assert _pairs(sent) == [
        ("human", "m2"),
        ("ai", "mentor answer to m2"),
        ("human", PLANNER_MSG + " v2"),
        ("ai", "PLAN-OUT"),
        ("human", "last"),
    ]
    assert all(m.type != "tool" and not getattr(m, "tool_calls", None) for m in sent)


# ------------------- review LOW: routing failure log is secret-safe ---------- #
def test_routing_failure_defaults_to_mentor_without_logging_provider_text(
    build, caplog, monkeypatch
):
    graph, _ = build([])

    class _Broken:
        def invoke(self, *a, **k):
            raise RuntimeError("PROVIDER-BODY api_key=sk-secret")

    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: _Broken())
    with caplog.at_level("WARNING", logger="src.graph.supervisor"):
        out = graph.invoke({"question": MENTOR_MSG}, _thread())
    assert out["answer"] == "mentor"
    assert "Supervisor routing failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "PROVIDER-BODY" not in caplog.text and "sk-secret" not in caplog.text


# ------------- review round 2 B1: the question is never streamed back ------- #
@pytest.mark.parametrize("route", ["planner", "evaluator", "current_affairs"])
def test_chat_endpoint_streams_only_the_specialist_answer(client, make_user, monkeypatch, route):
    # Specialists now store the student's question in ``messages`` (S1). LangGraph's
    # messages stream emits that HumanMessage under the specialist's node name, so
    # the endpoint must forward AI output only - never echo the question.
    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: ScriptedRouter([route]))

    def fake_plan(**kwargs):
        yield "PLAN-OUT"

    def fake_daily(date):
        yield "CA-OUT"

    monkeypatch.setattr(subgraphs, "generate_plan", fake_plan)
    monkeypatch.setattr(subgraphs, "get_daily_ca", fake_daily)
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=EVAL_TEXT))
    )
    tag = uuid.uuid4().hex[:8]  # unique text: never a response-cache hit
    question = {
        "planner": f"{PLANNER_MSG} ({tag})",
        "evaluator": f"Evaluate ({tag})\nQuestion: {QUESTION}\nAnswer: {ANSWER}",
        "current_affairs": f"{CA_MSG} ({tag})",
    }[route]
    expected = {"planner": "PLAN-OUT", "evaluator": EVAL_TEXT, "current_affairs": "CA-OUT"}[route]
    user = make_user(f"echo{route[:4]}")
    r = client.post(
        "/api/v1/agent/chat/stream",
        json={"question": question, "thread_id": f"echo-{tag}"},
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.text == expected
    assert tag not in r.text
