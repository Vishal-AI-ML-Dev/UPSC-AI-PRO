"""Offline tests for evaluator markdown -> Pydantic parsing (pure, no keys)."""

import pytest

from src.core.eval_parse import (
    parse_answer_evaluation,
    parse_mains_evaluation,
    parse_score,
)
from src.schemas import AnswerEvaluation, MainsEvaluation

BASIC = """## Score: 6/10

### What You Did Well ✓
- Defined the concept clearly
- Used a relevant example

### What's Missing ✗
- No constitutional articles
- Missing counter-arguments

### Structure Feedback
- **Introduction:** crisp
- **Body:** organized

### Keywords You Should Have Used
- separation of powers

### Model Answer (150-200 words)
Some model answer text here.

### Priority Improvements
1. Add articles
2. Add counter-arguments
3. Strengthen conclusion
"""

MAINS = """## 📊 Score: [7]/10
**Verdict:** A solid attempt with good structure but limited data.

### ✅ Strengths
- Clear introduction defining federalism
- Good use of Article 246

### ❌ Gaps
- No mention of GST Council
- Missing recent examples

### 🏗️ Structure
| Part | Feedback |
|------|----------|
| Introduction | Crisp |

### 🔑 Keyword Analysis
Present: federalism | Missing: cooperative

### 🎯 Top 3 Improvements
1. Add GST Council example
2. Cite Sarkaria Commission
3. Improve conclusion
"""


def test_parse_score_variants():
    assert parse_score("## Score: 6/10") == (6.0, 10)
    assert parse_score("## 📊 Score: [7]/10") == (7.0, 10)
    assert parse_score("Score: 8.5 / 15") == (8.5, 15)
    assert parse_score("no score here") == (None, None)
    assert parse_score("") == (None, None)


def test_parse_answer_evaluation():
    ev = parse_answer_evaluation(BASIC)
    assert isinstance(ev, AnswerEvaluation)
    assert ev.score == 6.0 and ev.max_score == 10
    assert len(ev.did_well) == 2
    assert "constitutional articles" in " ".join(ev.missing).lower()
    assert len(ev.improvements) == 3


def test_parse_mains_evaluation():
    ev = parse_mains_evaluation(MAINS, max_marks=10)
    assert isinstance(ev, MainsEvaluation)
    assert ev.score == 7.0 and ev.max_marks == 10
    assert ev.verdict and "solid attempt" in ev.verdict.lower()
    assert len(ev.strengths) == 2
    assert len(ev.gaps) == 2
    assert len(ev.improvements) == 3


def test_mains_uses_the_requested_max_and_rescales_a_different_written_max():
    # Phase 07 F6 (owner decision): the REQUESTED marks are the maximum. Before,
    # a model-written "12/15" on a 10-mark question returned 12/15; now it is
    # rescaled onto the requested 10 marks.
    ev = parse_mains_evaluation("## 📊 Score: 12/15", max_marks=10)
    assert ev.score == 8.0 and ev.max_marks == 10


def test_empty_and_garbage_safe():
    ev = parse_mains_evaluation("")
    assert ev.score is None and ev.strengths == [] and ev.gaps == []
    ev2 = parse_answer_evaluation("random text with no headings")
    assert ev2.score is None and ev2.did_well == [] and ev2.improvements == []


def test_gaps_not_polluted_by_keyword_analysis():
    ev = parse_mains_evaluation(MAINS)
    joined = " ".join(ev.gaps).lower()
    assert "gst council" in joined
    assert "cooperative" not in joined  # keyword-analysis line must not leak into gaps


# --- Phase 07 F6: scores never leave [0, max] --------------------------------
@pytest.mark.parametrize(
    "text, max_marks, expected",
    [
        ("## 📊 Score: 12/10", 10, None),  # above the max -> unscored, not 12/10
        ("## 📊 Score: 10/10", 10, 10.0),
        ("## 📊 Score: 0/10", 10, 0.0),
        ("## 📊 Score: 9/15", 15, 9.0),
        ("## 📊 Score: 6/10", 15, 9.0),  # rescaled onto the requested 15
        ("## 📊 Score: 18/15", 10, None),  # out of range even before rescaling
        ("## 📊 Score: 3/0", 10, None),  # zero maximum
        ("no score at all", 10, None),
    ],
)
def test_mains_score_is_bounded(text, max_marks, expected):
    ev = parse_mains_evaluation(text, max_marks=max_marks)
    assert ev.score == expected
    assert ev.max_marks == max_marks


@pytest.mark.parametrize(
    "text, expected",
    [
        ("## Score: 11/10", None),
        ("## Score: 7/10", 7.0),
        ("## Score: 4/5", 8.0),  # rescaled onto the basic evaluator's 10
        ("## Score: 2/0", None),
    ],
)
def test_basic_score_is_bounded(text, expected):
    ev = parse_answer_evaluation(text)
    assert ev.score == expected
    assert ev.max_score == 10
