"""PYQ parser schema and bank difficulty (PQ1-PQ2 of the 2026-10-29 audit), mocked."""

import re

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.pyq.graph as pyq
import src.core.vector_store as vector_store
from src.agents.pyq.prompts import PARSER_PROMPT


# ----------------------------- PQ1: schema shows objects -------------------- #
def test_parser_schema_is_an_array_of_objects():
    text = PARSER_PROMPT.format(text="1. Some question")
    schema = text.split("Schema", 1)[1].split("RULES:", 1)[0]
    assert re.search(r"\[\s*\{\s*\"question\"", schema)
    assert re.search(r"\"paper\"[^\n]*\n\s*\}\s*\]", schema)


# ----------------------------- PQ2: bank difficulty ------------------------- #
@pytest.fixture
def bank_prompts(monkeypatch):
    monkeypatch.setattr(vector_store, "vector_store_exists", lambda key: True)
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(vector_store, "similarity_search", lambda db, q, k, label: "Bank text")
    seen = []

    def reply(prompt_value):
        seen.append(prompt_value.to_string())
        return AIMessage(content="Q1. A practice question?\n(a) A (b) B")

    monkeypatch.setattr(pyq, "get_llm", lambda: RunnableLambda(reply))
    return seen


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_bank_generation_uses_selected_difficulty(bank_prompts, difficulty):
    list(pyq.generate_from_bank("user-1", topic="Polity", difficulty=difficulty))
    generation_prompt = bank_prompts[0]
    assert f"Difficulty: {difficulty}" in generation_prompt


def test_bank_generation_default_difficulty(bank_prompts):
    list(pyq.generate_from_bank("user-1", topic="Polity"))
    assert "Difficulty: medium" in bank_prompts[0]
