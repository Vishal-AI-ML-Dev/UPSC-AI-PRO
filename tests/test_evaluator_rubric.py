"""Evaluator scoring prompts (EV1-EV3 of the 2026-10-29 prompt audit), mocked.

The model is a stand-in that records the prompt; no provider call.
"""

import re

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

import src.agents.evaluator.graph as ev
from src.agents.evaluator.prompts import EVALUATOR_PROMPT

QUESTION = "Discuss the role of the Gram Sabha in rural local governance."
ANSWER = " ".join(["The Gram Sabha includes all adult voters of the village."] * 8)
RUBRIC_RE = {
    name: re.compile(re.escape(label) + r": ([\d.]+) marks")
    for name, label in [
        ("content", "Content & Coverage"),
        ("analysis", "Analysis & Insight"),
        ("structure", "Structure & Language"),
        ("keywords", "Keywords & Terminology"),
    ]
}


@pytest.fixture
def mains_prompt(monkeypatch):
    seen = []

    def stream(inputs):
        for value in inputs:
            seen.append(value.to_string())
        yield AIMessageChunk(content="## 📊 Score: 3/5")

    monkeypatch.setattr(ev, "get_llm", lambda: RunnableGenerator(stream))

    def run(marks):
        list(ev.evaluate_mains(QUESTION, ANSWER, marks=marks))
        return seen[-1]

    return run


def _rubric(prompt):
    return {name: float(rx.search(prompt).group(1)) for name, rx in RUBRIC_RE.items()}


# ----------------------------- EV2: rubric matches marks -------------------- #
@pytest.mark.parametrize("marks", range(5, 16))
def test_rubric_adds_up_to_the_question_marks(mains_prompt, marks):
    parts = _rubric(mains_prompt(marks))
    assert sum(parts.values()) == marks
    assert all(v > 0 for v in parts.values())


@pytest.mark.parametrize(
    "marks, expected",
    [(10, (4, 3, 2, 1)), (15, (6, 5, 3, 1))],
)
def test_existing_10_and_15_mark_rubrics_unchanged(mains_prompt, marks, expected):
    parts = _rubric(mains_prompt(marks))
    assert tuple(parts.values()) == expected


# ----------------------------- EV1: top band scales ------------------------- #
def test_15_mark_top_band_is_not_fixed_at_8(mains_prompt):
    prompt = mains_prompt(15)
    assert "reserve 8+" not in prompt
    assert "12 or more out of 15" in prompt


def test_10_mark_top_band_keeps_8(mains_prompt):
    assert "8 or more out of 10" in mains_prompt(10)


# ----------------------------- EV3: basic model answer guard ---------------- #
def test_basic_model_answer_has_accuracy_rules():
    text = EVALUATOR_PROMPT.format(question=QUESTION, answer=ANSWER, word_count=80)
    assert "Do NOT invent statistics" in text
    assert "write the point conceptually" in text
