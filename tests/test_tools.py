"""Offline tests for the tool-calling dispatch layer (src/graph/tools.py).

These cover the *pure* part of real tool-calling: mapping model-emitted tool
calls to results, and gracefully handling unknown tools / bad args / tool
exceptions. No LLM, no langchain, no vector store are imported, so this runs
fully offline with no API key (importing src.graph.tools must stay
dependency-free by design - heavy imports are lazy).
"""

from src.graph.tools import (
    DEFAULT_TOOL_SYSTEM,
    TOOL_FUNCTIONS,
    _compose_system,
    _content_to_text,
    _should_seed_question,
    run_tool_calls,
    web_search_tool_fn,
)


# A fake registry so we never touch the network or an LLM.
def _echo(query: str) -> str:
    return f"echo:{query}"


def _kb(query: str, persist_key: str = "") -> str:
    return f"kb:{query}:{persist_key}"


def _boom(**_kwargs) -> str:
    raise RuntimeError("kaboom")


FAKE = {"web_search": _echo, "knowledge_base_search": _kb, "explode": _boom}


def test_registry_names_match_structured_tool_contract():
    # The dispatch registry must expose exactly the tools the model is told about.
    assert set(TOOL_FUNCTIONS) == {"web_search", "knowledge_base_search"}


def test_dispatch_runs_tool_and_preserves_id():
    calls = [{"name": "web_search", "args": {"query": "prelims date"}, "id": "call_1"}]
    out = run_tool_calls(calls, registry=FAKE)
    assert out == [{"tool_call_id": "call_1", "name": "web_search", "content": "echo:prelims date"}]


def test_dispatch_passes_multiple_kwargs():
    calls = [
        {
            "name": "knowledge_base_search",
            "args": {"query": "polity", "persist_key": "ncert6"},
            "id": "c2",
        }
    ]
    out = run_tool_calls(calls, registry=FAKE)
    assert out[0]["content"] == "kb:polity:ncert6"


def test_optional_kwarg_defaults_apply():
    calls = [{"name": "knowledge_base_search", "args": {"query": "polity"}, "id": "c3"}]
    out = run_tool_calls(calls, registry=FAKE)
    assert out[0]["content"] == "kb:polity:"


def test_unknown_tool_becomes_error_not_exception():
    out = run_tool_calls([{"name": "nope", "args": {}, "id": "x"}], registry=FAKE)
    assert out[0]["content"].startswith("ERROR: unknown tool 'nope'")
    # It should advertise the tools that DO exist so the model can recover.
    assert "web_search" in out[0]["content"]


def test_tool_exception_becomes_error_not_exception():
    out = run_tool_calls([{"name": "explode", "args": {}, "id": "x"}], registry=FAKE)
    assert out[0]["content"].startswith("ERROR: tool 'explode' failed:")
    assert "kaboom" in out[0]["content"]


def test_bad_argument_name_becomes_error():
    calls = [{"name": "web_search", "args": {"wrong": "x"}, "id": "x"}]
    out = run_tool_calls(calls, registry=FAKE)
    assert out[0]["content"].startswith("ERROR: bad arguments for tool 'web_search'")


def test_non_dict_args_becomes_error():
    calls = [{"name": "web_search", "args": ["not", "a", "dict"], "id": "x"}]
    out = run_tool_calls(calls, registry=FAKE)
    assert out[0]["content"].startswith("ERROR: tool 'web_search' expects an object")


def test_multiple_calls_execute_in_order():
    calls = [
        {"name": "web_search", "args": {"query": "a"}, "id": "1"},
        {"name": "web_search", "args": {"query": "b"}, "id": "2"},
    ]
    out = run_tool_calls(calls, registry=FAKE)
    assert [r["tool_call_id"] for r in out] == ["1", "2"]
    assert [r["content"] for r in out] == ["echo:a", "echo:b"]


def test_empty_calls_returns_empty():
    assert run_tool_calls([], registry=FAKE) == []
    assert run_tool_calls(None, registry=FAKE) == []


# --- content normalization (Gemini returns content as a list of blocks) ---
def test_content_to_text_passthrough_string():
    assert _content_to_text("hello world") == "hello world"


def test_content_to_text_flattens_gemini_blocks():
    content = [
        {"type": "text", "text": "part1 ", "extras": {"signature": "abc"}},
        {"type": "text", "text": "part2"},
    ]
    assert _content_to_text(content) == "part1 part2"


def test_content_to_text_ignores_nontext_blocks_and_none():
    assert _content_to_text(None) == ""
    assert _content_to_text([{"type": "image", "url": "x"}, {"type": "text", "text": "ok"}]) == "ok"


# --- question seeding on a (possibly checkpointed) thread ---
class _FakeMsg:
    """Minimal duck-typed message: has .type and .content like langchain messages."""

    def __init__(self, type, content=""):
        self.type = type
        self.content = content


def test_seed_when_history_empty():
    assert _should_seed_question([], "hello") is True


def test_no_seed_without_question():
    assert _should_seed_question([], "") is False
    assert _should_seed_question([_FakeMsg("human", "x")], None) is False


def test_no_seed_when_returning_from_tools():
    hist = [_FakeMsg("human", "q"), _FakeMsg("ai"), _FakeMsg("tool", "result")]
    assert _should_seed_question(hist, "q") is False


def test_seed_new_question_on_existing_thread():
    # The exact Q2 bug: Q1 already answered on the thread, a NEW question arrives.
    hist = [_FakeMsg("human", "Motivate me"), _FakeMsg("ai", "...you got this")]
    assert _should_seed_question(hist, "When is UPSC Prelims 2026?") is True


def test_no_seed_when_question_already_last_human():
    hist = [_FakeMsg("human", "When is UPSC Prelims 2026?")]
    assert _should_seed_question(hist, "When is UPSC Prelims 2026?") is False


# --- truthfulness guardrail: no fabricating dated facts when search fails ---
def test_web_search_empty_query_is_safe():
    # Empty query never touches the network / heavy imports.
    assert web_search_tool_fn("") == "No query provided."
    assert web_search_tool_fn("   ") == "No query provided."


def test_system_prompt_forbids_fabricating_dated_facts():
    s = DEFAULT_TOOL_SYSTEM.lower()
    assert "web_search" in s
    # must instruct honest fallback to the official source
    assert "upsc.gov.in" in s or "official source" in s
    # must explicitly forbid guessing dated facts from memory
    assert "never" in s


# --------------------------------------------------------------------------- #
# system-prompt composition (mentor student-profile injection hook)
# --------------------------------------------------------------------------- #
def test_compose_system_appends_extra():
    assert _compose_system("BASE", "extra ctx") == "BASE\n\nextra ctx"


def test_compose_system_ignores_empty():
    assert _compose_system("BASE", "") == "BASE"
    assert _compose_system("BASE", None) == "BASE"
    assert _compose_system("BASE", "   ") == "BASE"
