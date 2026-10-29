"""Unit tests for the self-critique reflection loop (src.graph.reflection).

No LLM/network: only the PURE decision + formatting helpers are exercised, plus
the fail-open contract of the node when the critic is stubbed.
"""

from __future__ import annotations

from src.graph import reflection
from src.graph.reflection import (
    Critique,
    _feedback_block,
    make_reflect_node,
    should_revise,
)


def test_should_revise_stops_when_passing_and_high_score():
    assert (
        should_revise(passes=True, score=9, min_score=7, revisions_done=0, max_revisions=1) is False
    )


def test_should_revise_when_weak_and_budget_left():
    assert (
        should_revise(passes=False, score=4, min_score=7, revisions_done=0, max_revisions=1) is True
    )


def test_should_revise_when_low_score_even_if_passes():
    # A low score below the bar still triggers a revise even if passes=True.
    assert (
        should_revise(passes=True, score=5, min_score=7, revisions_done=0, max_revisions=1) is True
    )


def test_should_revise_respects_budget():
    assert (
        should_revise(passes=False, score=2, min_score=7, revisions_done=1, max_revisions=1)
        is False
    )


def test_should_revise_missing_score_uses_passes_flag():
    assert (
        should_revise(passes=True, score=None, min_score=7, revisions_done=0, max_revisions=1)
        is False
    )
    assert (
        should_revise(passes=False, score=None, min_score=7, revisions_done=0, max_revisions=1)
        is True
    )


def test_feedback_block_lists_issues_and_suggestions():
    c = Critique(passes=False, score=4, issues=["No examples"], suggestions=["Add case law"])
    block = _feedback_block(c)
    assert "No examples" in block
    assert "Add case law" in block


def test_feedback_block_default_when_empty():
    c = Critique(passes=True, score=9, issues=[], suggestions=[])
    assert _feedback_block(c).strip() != ""


def test_reflect_node_revises_when_weak(monkeypatch):
    # Critic says weak once, then passing; reviser marks the answer.
    calls = {"n": 0}

    def fake_critique(question, answer, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return Critique(passes=False, score=3, issues=["thin"], suggestions=["expand"])
        return Critique(passes=True, score=9, issues=[], suggestions=[])

    def fake_revise(question, answer, critique, **kwargs):
        return answer + " [revised]"

    monkeypatch.setattr(reflection, "critique_answer", fake_critique)
    monkeypatch.setattr(reflection, "revise_answer", fake_revise)

    node = make_reflect_node(min_score=7, max_revisions=1)
    out = node({"question": "Q", "answer": "draft"})
    assert out["answer"] == "draft [revised]"
    assert out["revision_count"] == 1


def test_reflect_node_noop_on_empty_answer():
    node = make_reflect_node()
    assert node({"question": "Q", "answer": ""}) == {}


def test_reflect_node_keeps_answer_when_already_good(monkeypatch):
    monkeypatch.setattr(
        reflection,
        "critique_answer",
        lambda q, a, **k: Critique(passes=True, score=10, issues=[], suggestions=[]),
    )
    node = make_reflect_node(min_score=7, max_revisions=2)
    out = node({"question": "Q", "answer": "solid answer"})
    assert out["answer"] == "solid answer"
    assert out["revision_count"] == 0
