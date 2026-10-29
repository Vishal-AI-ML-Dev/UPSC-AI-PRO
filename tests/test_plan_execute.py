"""Unit tests for plan-and-execute helpers (src.graph.plan_execute).

Pure helpers only: complexity gate, plan cleaning, and worklog formatting. No
LLM/network involved.
"""

from __future__ import annotations

from src.graph.plan_execute import (
    Plan,
    clamp_steps,
    format_worklog,
    is_complex,
)


def test_is_complex_long_question():
    q = " ".join(["word"] * 35)
    assert is_complex(q, min_words=30) is True


def test_is_complex_multipart_cue():
    assert is_complex("Compare NPS and OPS pension schemes.") is True
    assert is_complex("Discuss advantages and disadvantages of FPTP.") is True


def test_is_complex_multiple_questions():
    assert is_complex("What is inflation? How does RBI control it?") is True


def test_is_simple_short_atomic_question():
    assert is_complex("What is Article 21?") is False


def test_is_complex_empty():
    assert is_complex("") is False
    assert is_complex(None) is False


def test_clamp_steps_dedupes_and_caps():
    steps = ["  A ", "a", "", "B", "C", "D", "E", "F"]
    out = clamp_steps(steps, max_steps=3)
    assert out == ["A", "B", "C"]


def test_clamp_steps_drops_blanks():
    assert clamp_steps(["", "   ", None], max_steps=5) == []


def test_plan_schema_defaults_empty():
    assert Plan().steps == []


def test_format_worklog_pairs_steps_with_results():
    steps = ["Q1", "Q2"]
    results = [{"step": "Q1", "result": "A1"}, {"step": "Q2", "result": ""}]
    log = format_worklog(steps, results)
    assert "Step 1: Q1" in log
    assert "A1" in log
    assert "Step 2: Q2" in log
    assert "(no result)" in log
