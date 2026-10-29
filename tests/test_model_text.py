"""Model content -> text for NCERT notes / study aids, and secret-safe failure logs.

All model calls are RunnableLambda stand-ins; nothing is embedded, generated or
written. These tests do not prove what caused the live "Notes generation failed".
"""

import json
import logging

import pytest
from google.genai.errors import ClientError, ServerError
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.ncert.graph as ncert
import src.core.study_aids as aids
from src.core.llm import describe_exception, message_text

SECRET_KEY = "AIza" + "SyntheticKeyForTestOnly0123456789ab"
SECRET_PROMPT = "SYNTHETIC-PROMPT-77aa"
SECRET_MSG = "SYNTHETIC-PROVIDER-MESSAGE-91"


# --------------------------------------------------------------------------- #
# message_text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content,expected",
    [
        ("plain answer", "plain answer"),
        ("", ""),
        (None, ""),
        ([], ""),
        (
            [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world", "extras": {"signature": "x"}},
            ],
            "Hello world",
        ),
        (["a", "b"], "ab"),
        (
            [{"type": "reasoning", "reasoning": "hidden"}, {"type": "text", "text": "visible"}],
            "visible",
        ),
        (
            [
                {"type": "thinking", "thinking": "hidden", "text": "also hidden"},
                {"type": "text", "text": "ok"},
            ],
            "ok",
        ),
        ([{"type": "image_url", "image_url": {"url": "data:..."}}], ""),  # non-text only
        ([{"type": "text"}, {"type": "text", "text": None}, 42], ""),  # malformed blocks
        ({"type": "text", "text": "a dict is not a content list"}, ""),
        (12345, ""),
    ],
)
def test_message_text(content, expected):
    assert message_text(content) == expected


# --------------------------------------------------------------------------- #
# NCERT notes
# --------------------------------------------------------------------------- #
@pytest.fixture
def study_env(monkeypatch):
    state = {"content": "notes", "aids_called": 0, "indexed": 0}
    monkeypatch.setattr(
        ncert, "read_chapter_pages", lambda c, s, ch: ([(1, "Gram Sabha text. " * 20)], "/x/ch.pdf")
    )
    monkeypatch.setattr(
        ncert, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=state["content"]))
    )
    monkeypatch.setattr(ncert, "vector_store_exists", lambda key: False)
    monkeypatch.setattr(
        ncert, "upsert_documents", lambda key, docs, rebuild=False: state.update(indexed=len(docs))
    )

    def _aids(*a, **k):
        state["aids_called"] += 1
        return ("<mm/>", "<q/>")

    monkeypatch.setattr(aids, "generate_study_aids", _aids)
    return state


def test_notes_from_list_of_text_blocks(study_env):
    study_env["content"] = [
        {"type": "reasoning", "reasoning": "hidden"},
        {"type": "text", "text": "## Notes\n"},
        {"type": "text", "text": "- point", "extras": {"signature": "abc"}},
    ]
    out = ncert.generate_study_session("class6", "socialscience", "ch.pdf")
    assert out["notes"] == "## Notes\n- point"
    assert study_env["aids_called"] == 1 and study_env["indexed"] > 0


def test_notes_from_plain_string_unchanged(study_env):
    study_env["content"] = "  ## Notes  "
    assert ncert.generate_study_session("class6", "socialscience", "ch.pdf")["notes"] == "## Notes"


@pytest.mark.parametrize(
    "content", ["", "   ", [], [{"type": "reasoning", "reasoning": "only"}], None]
)
def test_empty_or_non_text_notes_fail_cleanly(study_env, caplog, content):
    study_env["content"] = content if content is not None else ""
    with caplog.at_level(logging.ERROR):
        out = ncert.generate_study_session("class6", "socialscience", "ch.pdf")
    assert out["notes"].startswith("⚠️ Notes generation failed")
    assert study_env["aids_called"] == 0  # aids skipped as before
    assert study_env["indexed"] > 0  # indexing still runs (existing behaviour)
    assert "ValueError" in caplog.text and "src/agents/ncert/graph.py" in caplog.text


def test_notes_failure_log_is_secret_safe(study_env, monkeypatch, caplog):
    body = {
        "error": {
            "code": 503,
            "status": "UNAVAILABLE",
            "message": f"{SECRET_MSG} {SECRET_KEY} prompt={SECRET_PROMPT}",
        }
    }

    def _boom(_):
        raise ServerError(503, body)

    monkeypatch.setattr(ncert, "get_llm", lambda: RunnableLambda(_boom))
    with caplog.at_level(logging.ERROR):
        out = ncert.generate_study_session("class6", "socialscience", "ch.pdf")
    assert out["notes"].startswith("⚠️ Notes generation failed")
    text = caplog.text
    assert "ServerError" in text and "http=503" in text and "status=UNAVAILABLE" in text
    assert "src/agents/ncert/graph.py" in text
    for secret in (SECRET_MSG, SECRET_KEY, SECRET_PROMPT, "Gram Sabha text"):
        assert secret not in text
    rec = [r for r in caplog.records if r.name == "src.agents.ncert.graph"][-1]
    assert getattr(rec, "safe_summary", False) is True and rec.exc_info is None


# --------------------------------------------------------------------------- #
# Study aids
# --------------------------------------------------------------------------- #
AIDS_JSON = json.dumps(
    {
        "central": "Panchayati Raj",
        "branches": [{"title": "Three tiers", "points": ["village", "block", "district"]}],
        "prelims": [
            {
                "q": "Who elects the Gram Panchayat?",
                "options": ["Gram Sabha", "MPs", "MLAs", "Courts"],
                "answer": "A",
                "explanation": "chapter",
            }
        ],
        "mains": [{"q": "Explain the three-tier system.", "approach": "tiers"}],
    }
)


@pytest.fixture
def aids_model(monkeypatch):
    state = {"content": AIDS_JSON}
    monkeypatch.setattr(
        aids, "get_fast_llm", lambda: RunnableLambda(lambda _: AIMessage(content=state["content"]))
    )
    return state


@pytest.mark.parametrize(
    "content",
    [
        AIDS_JSON,
        [{"type": "text", "text": AIDS_JSON}],
        [
            {"type": "reasoning", "reasoning": "x"},
            {"type": "text", "text": AIDS_JSON[:40]},
            {"type": "text", "text": AIDS_JSON[40:]},
        ],
        [{"type": "text", "text": "```json\n" + AIDS_JSON + "\n```"}],
    ],
)
def test_study_aids_accept_string_and_block_content(aids_model, content):
    aids_model["content"] = content
    mindmap, questions = aids.generate_study_aids("material", "topic", "GS2")
    assert "Three tiers" in mindmap and "Who elects the Gram Panchayat?" in questions


@pytest.mark.parametrize(
    "content", ["", [], [{"type": "reasoning", "reasoning": "only"}], "not json at all"]
)
def test_study_aids_empty_or_non_text_return_empty(aids_model, content):
    aids_model["content"] = content
    assert aids.generate_study_aids("material", "topic", "GS2") == ("", "")


def test_safe_json_rejects_non_string():
    assert aids._safe_json([{"type": "text", "text": "{}"}]) is None
    assert aids._safe_json(None) is None


def test_study_aids_failure_log_is_secret_safe(aids_model, monkeypatch, caplog):
    def _boom(_):
        raise ClientError(
            400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": SECRET_MSG}}
        )

    monkeypatch.setattr(aids, "get_fast_llm", lambda: RunnableLambda(_boom))
    with caplog.at_level(logging.ERROR):
        assert aids.generate_study_aids(f"material {SECRET_PROMPT}", "t", "p") == ("", "")
    assert (
        "ClientError" in caplog.text
        and "http=400" in caplog.text
        and "src/core/study_aids.py" in caplog.text
    )
    assert SECRET_MSG not in caplog.text and SECRET_PROMPT not in caplog.text


# --------------------------------------------------------------------------- #
# describe_exception
# --------------------------------------------------------------------------- #
def test_describe_exception_never_includes_messages():
    try:
        try:
            raise ServerError(
                504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": SECRET_MSG}}
            )
        except ServerError as inner:
            raise RuntimeError(f"wrapped {SECRET_KEY} {SECRET_PROMPT}") from inner
    except RuntimeError as exc:
        s = describe_exception(exc)
    assert s.startswith("RuntimeError>ServerError ")
    assert "http=504" in s and "status=DEADLINE_EXCEEDED" in s
    for secret in (SECRET_MSG, SECRET_KEY, SECRET_PROMPT, "wrapped"):
        assert secret not in s


def test_describe_exception_ignores_non_token_status():
    class Odd(Exception):
        status = "lowercase message with spaces"
        code = "not-an-int"

    try:
        raise Odd(SECRET_MSG)
    except Odd as exc:
        s = describe_exception(exc)
    assert "http=- status=-" in s and SECRET_MSG not in s and "lowercase" not in s
