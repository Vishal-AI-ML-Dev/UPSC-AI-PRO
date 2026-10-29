"""Offline tests for evaluator markdown -> Pydantic parsing (pure, no keys)."""

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


def test_mains_max_marks_from_text_overrides_default():
    ev = parse_mains_evaluation("## 📊 Score: 12/15", max_marks=10)
    assert ev.score == 12.0 and ev.max_marks == 15


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
