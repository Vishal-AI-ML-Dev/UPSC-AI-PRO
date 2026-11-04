"""Planner + Answer Evaluator flows, end to end through the API, fully mocked.

The model is a RunnableLambda stand-in (no provider call), web search and the
mentor knowledge base are patched out. The block-content, log-safety and size
tests were strict xfails (gaps found in the 2026-10-29 review) until fixed.
"""

import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.evaluator.graph as evaluator
import src.agents.planner.graph as planner

API = "/api/v1"
QUESTION = "Discuss the role of the Gram Sabha in rural local governance."
ANSWER = " ".join(["The Gram Sabha includes all adult voters of the village."] * 8)  # 80 words

EVAL_TEXT = """## Score: 6/10

### What you did well
- Clear introduction
- Correct definition

### What's missing
- No constitutional reference

### Improvements
1. Add an example
"""

MAINS_TEXT = """## Score: 9/15
**Verdict:** Above average

### Strengths
- Good structure

### Gaps
- Missing data

### Top 3 Improvements
1. Add a diagram
"""

ENDPOINTS = [
    ("planner", "generate_plan", f"{API}/planner/generate", {"goal": "UPSC 2027"}),
    ("planner", "generate_plan", f"{API}/planner/generate/sync", {"goal": "UPSC 2027"}),
    (
        "evaluator",
        "evaluate_answer",
        f"{API}/evaluator/evaluate/sync",
        {"question": QUESTION, "answer": ANSWER},
    ),
    (
        "evaluator",
        "evaluate_mains",
        f"{API}/evaluator/mains/sync",
        {"question": QUESTION, "answer": ANSWER},
    ),
    ("evaluator", "get_model_answer", f"{API}/evaluator/model-answer/sync", {"question": QUESTION}),
]


def _model(content=None, exc=None, seen=None):
    def _f(prompt_value):
        if seen is not None:
            seen.append(prompt_value.to_string())
        if exc is not None:
            raise exc
        return AIMessage(content=content)

    return lambda: RunnableLambda(_f)


@pytest.fixture
def offline_planner(monkeypatch):
    monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: "")
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: False)


@pytest.fixture
def user(make_user):
    return make_user("pe")


# --------------------------------------------------------------------------- #
# 1. Authentication: every Planner/Evaluator endpoint rejects before any work
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module, attr, url, body", ENDPOINTS, ids=[e[2] for e in ENDPOINTS])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_unauthenticated_rejected_before_agent(
    client, monkeypatch, module, attr, url, body, headers
):
    import importlib

    calls = []
    monkeypatch.setattr(
        importlib.import_module(f"src.api.routes.{module}"),
        attr,
        lambda *a, **k: calls.append(attr) or iter(()),
    )
    r = client.post(url, json=body, headers=headers)
    assert r.status_code == 401
    assert calls == []


# --------------------------------------------------------------------------- #
# 2. Input validation (schema level, before the agent)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, body",
    [
        (f"{API}/planner/generate/sync", {}),  # goal required
        (f"{API}/evaluator/evaluate/sync", {"question": QUESTION, "answer": "too short"}),
        (f"{API}/evaluator/evaluate/sync", {"question": "Hi", "answer": ANSWER}),
        (f"{API}/evaluator/mains/sync", {"question": QUESTION, "answer": ANSWER, "marks": 20}),
        (f"{API}/evaluator/mains/sync", {"question": QUESTION, "answer": ANSWER, "marks": 4}),
        (f"{API}/evaluator/model-answer/sync", {"question": "Hi"}),
    ],
)
def test_invalid_input_is_422_without_model_call(client, user, monkeypatch, url, body):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model("x", seen=seen))
    monkeypatch.setattr(planner, "get_llm", _model("x", seen=seen))
    r = client.post(url, json=body, headers=user.headers)
    assert r.status_code == 422
    assert seen == []


def test_answer_under_50_words_gets_message_not_model_call(client, user, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT, seen=seen))
    short = "word " * 20  # >= 50 chars (schema) but < 50 words (agent)
    r = client.post(
        f"{API}/evaluator/evaluate/sync",
        json={"question": QUESTION, "answer": short},
        headers=user.headers,
    )
    assert r.status_code == 200
    assert "Answer too short" in r.json()["response"]
    assert r.json()["structured"]["score"] is None
    assert seen == []


def test_oversized_answer_is_rejected(client, user, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT, seen=seen))
    r = client.post(
        f"{API}/evaluator/evaluate/sync",
        json={"question": QUESTION, "answer": "word " * 40_000},
        headers=user.headers,
    )
    assert r.status_code == 422 and seen == []


def test_model_answer_marks_are_bounded(client, user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model("ok"))
    r = client.post(
        f"{API}/evaluator/model-answer/sync",
        json={"question": QUESTION, "marks": 1000, "word_limit": 100000},
        headers=user.headers,
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 3. Response parsing (string content)
# --------------------------------------------------------------------------- #
def test_evaluate_sync_parses_structured_result(client, user, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT, seen=seen))
    r = client.post(
        f"{API}/evaluator/evaluate/sync",
        json={"question": QUESTION, "answer": ANSWER},
        headers=user.headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["response"] == EVAL_TEXT
    s = body["structured"]
    assert (s["score"], s["max_score"]) == (6.0, 10)
    assert s["did_well"] == ["Clear introduction", "Correct definition"]
    assert s["missing"] == ["No constitutional reference"]
    assert s["improvements"] == ["Add an example"]
    assert len(seen) == 1 and QUESTION in seen[0]


def test_mains_sync_parses_structured_result_and_keywords(client, user, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model(MAINS_TEXT, seen=seen))
    r = client.post(
        f"{API}/evaluator/mains/sync",
        json={
            "question": QUESTION,
            "answer": ANSWER,
            "marks": 15,
            "keywords": ["Gram Sabha", "73rd Amendment"],
        },
        headers=user.headers,
    )
    assert r.status_code == 200
    s = r.json()["structured"]
    assert (s["score"], s["max_marks"], s["verdict"]) == (9.0, 15, "Above average")
    assert s["strengths"] == ["Good structure"] and s["gaps"] == ["Missing data"]
    assert "Missing: 73rd Amendment" in seen[0]


def test_model_answer_has_disclaimer(client, user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model("Intro. Body. Conclusion."))
    r = client.post(
        f"{API}/evaluator/model-answer/sync", json={"question": QUESTION}, headers=user.headers
    )
    assert r.status_code == 200
    text = r.json()["response"]
    assert text.startswith("> ⚠️ AI-generated model answer")
    assert text.endswith("Intro. Body. Conclusion.")


def test_planner_sync_and_stream_return_plan(client, user, monkeypatch, offline_planner):
    seen = []
    monkeypatch.setattr(planner, "get_llm", _model("## Month 1\n- Polity", seen=seen))
    body = {"goal": "UPSC 2027", "hours": "6", "weak": "Economy"}
    r = client.post(f"{API}/planner/generate/sync", json=body, headers=user.headers)
    assert r.status_code == 200
    assert r.json()["plan"] == "## Month 1\n- Polity"
    assert r.json()["meta"]["attempt_year"] == 2027
    s = client.post(f"{API}/planner/generate", json=body, headers=user.headers)
    assert s.status_code == 200 and s.text == "## Month 1\n- Polity"
    assert len(seen) == 2 and "Economy" in seen[0]


def test_unparseable_evaluation_gives_empty_structure(client, user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model("Looks fine overall."))
    r = client.post(
        f"{API}/evaluator/evaluate/sync",
        json={"question": QUESTION, "answer": ANSWER},
        headers=user.headers,
    )
    s = r.json()["structured"]
    assert r.status_code == 200 and s["score"] is None and s["did_well"] == []


# --------------------------------------------------------------------------- #
# 4. Gemini-style block content (list of parts) - NCERT needed message_text()
# --------------------------------------------------------------------------- #
BLOCKS = [{"type": "text", "text": "## Score: 7/10\n"}, {"type": "text", "text": "Good."}]


def test_evaluate_accepts_block_content(client, user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(BLOCKS))
    r = client.post(
        f"{API}/evaluator/evaluate/sync",
        json={"question": QUESTION, "answer": ANSWER},
        headers=user.headers,
    )
    assert r.status_code == 200 and r.json()["structured"]["score"] == 7.0


def test_planner_sync_accepts_block_content(client, user, monkeypatch, offline_planner):
    monkeypatch.setattr(planner, "get_llm", _model(BLOCKS))
    r = client.post(
        f"{API}/planner/generate/sync", json={"goal": "UPSC 2027"}, headers=user.headers
    )
    assert r.status_code == 200 and r.json()["plan"] == "## Score: 7/10\nGood."


def test_model_answer_accepts_block_content(client, user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(BLOCKS))
    r = client.post(
        f"{API}/evaluator/model-answer/sync", json={"question": QUESTION}, headers=user.headers
    )
    assert r.status_code == 200 and r.json()["response"].endswith("Good.")


# --------------------------------------------------------------------------- #
# 5. Failure handling
# --------------------------------------------------------------------------- #
# Synthetic, non-secret provider message; it must never reach a log line.
SECRET_MSG = "upstream said: quota exceeded for project PROVIDER-DETAIL-42"


@pytest.mark.parametrize(
    "url, body, warning",
    [
        (
            f"{API}/evaluator/evaluate/sync",
            {"question": QUESTION, "answer": ANSWER},
            "Evaluation failed",
        ),
        (
            f"{API}/evaluator/mains/sync",
            {"question": QUESTION, "answer": ANSWER},
            "Evaluation failed",
        ),
        (f"{API}/evaluator/model-answer/sync", {"question": QUESTION}, "Could not generate"),
        (f"{API}/planner/generate/sync", {"goal": "UPSC 2027"}, "Plan generation failed"),
    ],
)
def test_model_failure_returns_warning_text(
    client, user, monkeypatch, offline_planner, url, body, warning
):
    monkeypatch.setattr(evaluator, "get_llm", _model(exc=RuntimeError(SECRET_MSG)))
    monkeypatch.setattr(planner, "get_llm", _model(exc=RuntimeError(SECRET_MSG)))
    r = client.post(url, json=body, headers=user.headers)
    assert r.status_code == 200  # failure is reported in-band, not as an HTTP error
    data = r.json()
    text = data.get("response") or data.get("plan")
    assert warning in text and SECRET_MSG not in text
    if "structured" in data:
        assert data["structured"]["score"] is None


def test_failure_logs_do_not_include_provider_message(monkeypatch, caplog):
    monkeypatch.setattr(evaluator, "get_llm", _model(exc=RuntimeError(SECRET_MSG)))
    with caplog.at_level(logging.ERROR):
        "".join(evaluator.evaluate_answer(QUESTION, ANSWER))
    assert "Evaluation failed" in caplog.text  # the log line was captured
    assert SECRET_MSG not in caplog.text


# --------------------------------------------------------------------------- #
# 6. User isolation: no server-side state; each request sees only its own input
# --------------------------------------------------------------------------- #
def test_requests_from_two_users_do_not_mix(client, make_user, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT, seen=seen))
    a, b = make_user("pa"), make_user("pb")
    ans_a = ANSWER + " ALPHA-ONLY"
    ans_b = ANSWER + " BRAVO-ONLY"
    for u, ans in ((a, ans_a), (b, ans_b)):
        r = client.post(
            f"{API}/evaluator/evaluate/sync",
            json={"question": QUESTION, "answer": ans},
            headers=u.headers,
        )
        assert r.status_code == 200
    assert "ALPHA-ONLY" in seen[0] and "BRAVO-ONLY" not in seen[0]
    assert "BRAVO-ONLY" in seen[1] and "ALPHA-ONLY" not in seen[1]


# --------------------------------------------------------------------------- #
# 7. Content shapes: mixed blocks, empty / non-text content (agents called directly)
# --------------------------------------------------------------------------- #
MIXED = [
    {"type": "reasoning", "reasoning": "hidden chain of thought"},
    "## Score: 8/10\n",
    {"type": "text", "text": "Strong answer."},
    {"type": "image_url", "image_url": "x"},
    {"type": "thinking", "text": "should not appear"},
]


def _run_all(monkeypatch, content=None, exc=None):
    """Text produced by each of the four agent generators for one model output."""
    monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: "")
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: False)
    monkeypatch.setattr(evaluator, "get_llm", _model(content, exc=exc))
    monkeypatch.setattr(planner, "get_llm", _model(content, exc=exc))
    gens = {
        "answer": evaluator.evaluate_answer(QUESTION, ANSWER),
        "mains": evaluator.evaluate_mains(QUESTION, ANSWER),
        "model": evaluator.get_model_answer(QUESTION),
        "plan": planner.generate_plan("UPSC 2027", "6", "", "", "1"),
    }
    out = {}
    for name, gen in gens.items():
        pieces = list(gen)
        assert all(isinstance(p, str) for p in pieces), name  # never a list/dict
        out[name] = "".join(pieces)
    return out


def test_mixed_blocks_keep_only_text(monkeypatch):
    out = _run_all(monkeypatch, MIXED)
    for name, text in out.items():
        assert text.endswith("## Score: 8/10\nStrong answer."), name
        assert "hidden" not in text and "should not appear" not in text, name
        assert "image_url" not in text and "{" not in text, name


@pytest.mark.parametrize(
    "content",
    [
        "",
        [],
        [{"type": "reasoning", "reasoning": "r"}],
        [{"type": "image_url", "image_url": "x"}],
        [{"type": "text", "text": ""}],
    ],
    ids=["empty-str", "empty-list", "reasoning-only", "non-text", "empty-text-block"],
)
def test_empty_or_non_text_content_gives_failure_message(monkeypatch, content):
    out = _run_all(monkeypatch, content)
    # Failure fallbacks are stream notices (Phase 08 Slice A, Amendment A-1).
    assert out["answer"] == "\n\n⚠️ Evaluation failed. Please try again."
    assert out["mains"] == "\n\n⚠️ Evaluation failed. Please try again."
    assert out["model"].endswith("\n\n⚠️ Could not generate model answer. Please try again.")
    assert out["plan"] == "\n\n⚠️ Plan generation failed. Please try again."


def test_all_failure_logs_are_sanitized(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _run_all(monkeypatch, exc=RuntimeError(SECRET_MSG))
    for label in (
        "Evaluation failed",
        "Mains evaluation failed",
        "Model answer generation failed",
        "Plan generation failed",
    ):
        assert label in caplog.text, label
    assert "RuntimeError" in caplog.text
    assert SECRET_MSG not in caplog.text and "PROVIDER-DETAIL" not in caplog.text
    assert QUESTION not in caplog.text  # prompt text is not logged either


def test_planner_kb_failure_log_is_sanitized(monkeypatch, caplog):
    monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: "")
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError(SECRET_MSG)

    monkeypatch.setattr(planner.mentor_kb, "search_kb", _boom)
    monkeypatch.setattr(planner, "get_llm", _model("plan"))
    with caplog.at_level(logging.WARNING):
        assert "".join(planner.generate_plan("UPSC 2027", "6", "", "", "1")) == "plan"
    assert "Planner KB search failed" in caplog.text and SECRET_MSG not in caplog.text


# --------------------------------------------------------------------------- #
# 8. Size bounds (boundary values)
# --------------------------------------------------------------------------- #
def _post(client, user, path, body):
    return client.post(f"{API}{path}", json=body, headers=user.headers)


@pytest.fixture
def ok_models(monkeypatch, offline_planner):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    monkeypatch.setattr(planner, "get_llm", _model("plan"))


def _text(n_chars):
    return ("w " * n_chars)[:n_chars]


@pytest.mark.parametrize(
    "path, base, field, limit",
    [
        ("/evaluator/evaluate/sync", {"question": QUESTION, "answer": ANSWER}, "question", 2000),
        ("/evaluator/evaluate/sync", {"question": QUESTION, "answer": ANSWER}, "answer", 20000),
        ("/evaluator/mains/sync", {"question": QUESTION, "answer": ANSWER}, "question", 2000),
        ("/evaluator/mains/sync", {"question": QUESTION, "answer": ANSWER}, "answer", 20000),
        ("/evaluator/model-answer/sync", {"question": QUESTION}, "question", 2000),
        ("/planner/generate/sync", {"goal": "UPSC 2027"}, "goal", 200),
        ("/planner/generate/sync", {"goal": "UPSC 2027"}, "hours", 200),
        ("/planner/generate/sync", {"goal": "UPSC 2027"}, "optional", 200),
        ("/planner/generate/sync", {"goal": "UPSC 2027"}, "weak", 200),
    ],
)
def test_text_length_boundaries(client, user, ok_models, path, base, field, limit):
    assert _post(client, user, path, {**base, field: _text(limit)}).status_code == 200
    assert _post(client, user, path, {**base, field: _text(limit + 1)}).status_code == 422


@pytest.mark.parametrize("path", ["/evaluator/mains/sync", "/evaluator/model-answer/sync"])
def test_marks_word_limit_and_keyword_boundaries(client, user, ok_models, path):
    base = {"question": QUESTION, "answer": ANSWER}
    ok = [
        {"marks": 5},
        {"marks": 15},
        {"word_limit": 1},
        {"word_limit": 1000},
        {"keywords": ["k"] * 20},
        {"keywords": ["k" * 100]},
    ]
    bad = [
        {"marks": 4},
        {"marks": 16},
        {"word_limit": 0},
        {"word_limit": 1001},
        {"keywords": ["k"] * 21},
        {"keywords": ["k" * 101]},
    ]
    for extra in ok:
        assert _post(client, user, path, {**base, **extra}).status_code == 200, extra
    for extra in bad:
        assert _post(client, user, path, {**base, **extra}).status_code == 422, extra


def test_frontend_default_requests_still_valid(client, user, ok_models):
    """Shapes the UI sends today (Evaluator.tsx / Planner.tsx) stay accepted."""
    plan = {
        "goal": "UPSC 2026",
        "hours": "6",
        "optional": "PSIR (Political Science & IR)",
        "weak": "Not specified",
        "attempt_number": "4+",
    }
    assert _post(client, user, "/planner/generate", plan).status_code == 200
    mains = {
        "question": QUESTION,
        "answer": ANSWER,
        "marks": 10,
        "word_limit": 150,
        "keywords": ["Gram Sabha"],
    }
    assert _post(client, user, "/evaluator/mains/sync", mains).status_code == 200
    model = {"question": QUESTION, "marks": 15, "word_limit": 400, "keywords": ["Gram Sabha"]}
    assert _post(client, user, "/evaluator/model-answer/sync", model).status_code == 200
