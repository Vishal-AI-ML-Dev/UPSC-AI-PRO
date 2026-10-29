"""Phase 2 batch 3 (2026-10-29 prompt audit MT1, CA3, PYQ bank wording), mocked.

Prompt-wording tests check the instructions/messages; they cannot prove what a
live model writes. No provider, store or network call.
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.pyq.graph as pyq
import src.core.vector_store as vector_store
from src.agents.current_affairs.prompts import DAILY_CA_PROMPT
from src.graph import tools


# ----------------------------- MT1: Mentor fallback ------------------------- #
def _assert_labelled_fallback(text):
    assert "FULLY AND CONFIDENTLY" not in text
    assert "general knowledge" in text
    assert "standard source" in text


def test_kb_empty_note_asks_to_label_memory_answers():
    _assert_labelled_fallback(tools.KB_FALLBACK_NOTE)
    # Unchanged guardrails:
    assert "do NOT mention this note" in tools.KB_FALLBACK_NOTE
    assert "defer to official sources" in tools.KB_FALLBACK_NOTE


def test_kb_outage_note_asks_to_label_memory_answers(monkeypatch):
    from src.core import mentor_kb

    def boom(query, k=4):
        raise RuntimeError("store down")

    monkeypatch.setattr(mentor_kb, "search_kb", boom)
    _assert_labelled_fallback(tools.knowledge_base_tool_fn("What is federalism?"))


def test_system_prompt_fallback_asks_to_label_memory_answers():
    s = tools.DEFAULT_TOOL_SYSTEM
    assert "general knowledge" in s
    assert "standard source" in s
    # Existing dated-facts guardrail unchanged:
    assert "upsc.gov.in" in s and "NEVER state or guess such dated facts" in s


# ----------------------------- CA3: daily template -------------------------- #
@pytest.fixture
def daily_prompt():
    return DAILY_CA_PROMPT.format(date="29 October 2026", news_context="HEADLINES")


def test_prelims_angle_only_when_snippet_supports_it(daily_prompt):
    assert "(One crisp MCQ-worthy fact)" not in daily_prompt
    assert "ONLY if the headline/snippet states one" in daily_prompt


def test_key_developments_not_forced_to_three(daily_prompt):
    assert "Development 3" not in daily_prompt
    assert "only what the headline/snippet states" in daily_prompt


def test_syllabus_connect_is_not_official_wording(daily_prompt):
    assert "[exact syllabus point]" not in daily_prompt
    assert "not official syllabus wording" in daily_prompt


def test_exam_weight_is_marked_as_estimate(daily_prompt):
    assert "Exam Weight (estimate)" in daily_prompt


# ----------------------------- PYQ bank wording ----------------------------- #
@pytest.fixture
def bank_output(monkeypatch):
    monkeypatch.setattr(vector_store, "vector_store_exists", lambda key: True)
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(vector_store, "similarity_search", lambda db, q, k, label: "Bank text")
    monkeypatch.setattr(
        pyq,
        "get_llm",
        lambda: RunnableLambda(lambda _: AIMessage(content="Q1. A question?\n(a) A (b) B")),
    )
    return "".join(pyq.generate_from_bank("user-1", topic="Polity"))


def test_bank_banner_says_answers_are_ai_written(bank_output):
    assert "answers and explanations are AI-generated" in bank_output
    assert "not taken from your papers" in bank_output
    assert "mirror the real PYQs" not in bank_output
