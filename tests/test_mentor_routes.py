"""Mentor chat sub-routes get usable inputs from the free-text question (mocked).

``/agent/chat/stream`` only sends ``question``; the planner, evaluator and
current-affairs subgraphs used to read everything from ``task_inputs`` (never
set by chat), and the rag route needs a ``persist_key`` (never set by chat).
No provider, web or vector-store call is made.
"""

import uuid

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.current_affairs.graph as ca
import src.agents.evaluator.graph as evaluator
import src.graph.agent_subgraphs as subgraphs
import src.graph.supervisor as supervisor


def today_label():
    return ca._today_ist().strftime("%d %B %Y")


ANSWER = " ".join(["The Gram Sabha includes all adult voters of the village."] * 8)  # 80 words
QUESTION = "Discuss the role of the Gram Sabha in rural local governance."
EVAL_TEXT = "## Score: 6/10\n\n### What You Did Well\n- Clear definition\n"


def _capture(monkeypatch, name):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        yield "OUT"

    monkeypatch.setattr(subgraphs, name, fake)
    return calls


# ----------------------------- evaluator (MC1) ------------------------------ #
def test_evaluator_route_uses_question_and_answer_from_message(monkeypatch):
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=EVAL_TEXT))
    )
    msg = f"Please evaluate my answer.\nQuestion: {QUESTION}\nAnswer: {ANSWER}"
    out = subgraphs.build_evaluator_subgraph().invoke({"question": msg})
    assert out["answer"] == EVAL_TEXT
    assert "Please write an answer" not in out["answer"]


def test_evaluator_route_passes_split_text(monkeypatch):
    calls = _capture(monkeypatch, "evaluate_answer")
    msg = f"Check this. Q: {QUESTION}\nAns: {ANSWER}"
    subgraphs.build_evaluator_subgraph().invoke({"question": msg})
    kwargs = calls[0][1]
    assert kwargs["question"] == QUESTION
    assert kwargs["answer"] == ANSWER


def test_evaluator_route_without_markers_explains_format(monkeypatch):
    calls = _capture(monkeypatch, "evaluate_answer")
    out = subgraphs.build_evaluator_subgraph().invoke({"question": "Can you check my answer?"})
    assert calls == []
    assert "Question:" in out["answer"] and "Answer:" in out["answer"]
    assert "Please write an answer" not in out["answer"]


# ----------------------------- planner (MC2) -------------------------------- #
@pytest.mark.parametrize(
    "msg, hours",
    [
        ("Make my plan for UPSC 2027, I can study 8 hours a day", "8"),
        ("Mera 2027 ka plan bana do, 8 ghante padh sakta hu", "8"),
        ("study plan, 5.5 hrs daily, 2nd attempt", "5.5"),
    ],
)
def test_planner_route_reads_hours_from_message(monkeypatch, msg, hours):
    calls = _capture(monkeypatch, "generate_plan")
    subgraphs.build_planner_subgraph().invoke({"question": msg})
    kwargs = calls[0][1]
    assert kwargs["hours"] == hours
    assert kwargs["goal"] == msg


def test_planner_route_reads_attempt_number(monkeypatch):
    calls = _capture(monkeypatch, "generate_plan")
    subgraphs.build_planner_subgraph().invoke({"question": "plan for 2027, 6 hours, 2nd attempt"})
    assert calls[0][1]["attempt_number"] == "2"


def test_planner_route_without_hours_asks_instead_of_assuming(monkeypatch):
    calls = _capture(monkeypatch, "generate_plan")
    out = subgraphs.build_planner_subgraph().invoke({"question": "Make a study plan for UPSC 2027"})
    assert calls == []
    assert "hours" in out["answer"].lower()


# ----------------------------- current affairs (MC3) ------------------------ #
def test_ca_route_monthly_request_gets_monthly_digest(monkeypatch):
    monthly = _capture(monkeypatch, "get_monthly_summary")
    daily = _capture(monkeypatch, "get_daily_ca")
    subgraphs.build_current_affairs_subgraph().invoke(
        {"question": "Give me the monthly current affairs for March 2026"}
    )
    assert daily == []
    assert monthly[0][0] == ("March", "2026")


def test_ca_route_editorial_request_gets_topic(monkeypatch):
    editorial = _capture(monkeypatch, "get_editorial")
    subgraphs.build_current_affairs_subgraph().invoke(
        {"question": "editorial analysis on judicial reforms"}
    )
    assert editorial[0][0] == ("judicial reforms",)


def test_ca_route_daily_uses_today(monkeypatch):
    daily = _capture(monkeypatch, "get_daily_ca")
    subgraphs.build_current_affairs_subgraph().invoke({"question": "aaj ke current affairs do"})
    assert daily[0][0] == (today_label(),)


# ----------------------------- rag (MC4) ------------------------------------ #
@pytest.fixture
def stub_supervisor(monkeypatch):
    """Supervisor with every specialist replaced by a node naming itself."""

    def node(name):
        return lambda state: {"answer": name}

    monkeypatch.setattr(supervisor, "build_mentor_graph", lambda: node("mentor"))
    monkeypatch.setattr(supervisor, "build_rag_subgraph", lambda label: node("rag"))
    monkeypatch.setattr(supervisor, "build_planner_subgraph", lambda: node("planner"))
    monkeypatch.setattr(supervisor, "build_evaluator_subgraph", lambda: node("evaluator"))
    monkeypatch.setattr(
        supervisor, "build_current_affairs_subgraph", lambda: node("current_affairs")
    )
    return supervisor.build_supervisor()


def test_rag_route_without_document_goes_to_mentor(stub_supervisor):
    out = stub_supervisor.invoke({"question": "explain NCERT class 6 chapter 11", "route": "rag"})
    assert out["answer"] == "mentor"


def test_rag_route_with_document_stays_rag(stub_supervisor):
    out = stub_supervisor.invoke({"question": "q", "route": "rag", "persist_key": "ncert_x"})
    assert out["answer"] == "rag"


# ----------------------------- end to end (chat endpoint) ------------------- #
def test_chat_endpoint_evaluator_route_evaluates_the_answer(client, make_user, monkeypatch):
    class _Router:
        def invoke(self, *a, **k):
            return supervisor.RouteDecision(route="evaluator")

    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: _Router())
    monkeypatch.setattr(
        evaluator, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=EVAL_TEXT))
    )
    user = make_user("mentorroute")
    msg = f"Evaluate ({uuid.uuid4().hex[:6]})\nQuestion: {QUESTION}\nAnswer: {ANSWER}"
    r = client.post("/api/v1/agent/chat/stream", json={"question": msg}, headers=user.headers)
    assert r.status_code == 200
    assert "Score: 6/10" in r.text
    assert "Please write an answer" not in r.text
