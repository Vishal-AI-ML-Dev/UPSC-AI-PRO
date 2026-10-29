"""Smoke: PYQ parser structures pasted text (LLM mocked both ways).

The parser tries the LLM first, then falls back to a deterministic regex
parser. We test BOTH branches without any network:
  1. LLM unavailable  -> regex fallback still returns structured questions.
  2. LLM returns JSON -> JSON path is parsed and normalised.
"""

from langchain_core.runnables import RunnableLambda


class _Msg:
    """Minimal stand-in for a LangChain AIMessage (only .content is used)."""

    def __init__(self, content):
        self.content = content


def test_regex_fallback_when_llm_unavailable(monkeypatch):
    import src.agents.pyq.graph as g

    def _boom():
        raise RuntimeError("LLM unavailable in test")

    monkeypatch.setattr(g, "get_llm", _boom)

    text = (
        "1. With reference to Article 370, consider the following.\n"
        "(a) Option one\n(b) Option two\n(c) Option three\n(d) Option four\n"
        "Answer: (a)\n"
        "2. Critically examine Indian federalism. (15 marks)\n"
    )
    questions = g.parse_questions(text)

    assert len(questions) >= 2
    prelims = questions[0]
    assert prelims["type"] == "prelims"
    assert isinstance(prelims["options"], list) and len(prelims["options"]) >= 2
    assert questions[1]["type"] == "mains"


def test_llm_json_path(monkeypatch):
    import src.agents.pyq.graph as g

    payload = (
        '[{"question": "What is the capital of India?", "type": "prelims", '
        '"options": ["Delhi", "Mumbai", "Kolkata", "Chennai"], "answer": "Delhi"}]'
    )
    monkeypatch.setattr(g, "get_llm", lambda: RunnableLambda(lambda _pv: _Msg(payload)))

    questions = g.parse_questions("any pasted text")
    assert len(questions) == 1
    assert questions[0]["question"].startswith("What is the capital")
    assert questions[0]["type"] == "prelims"
    assert questions[0]["options"][0] == "Delhi"


def test_empty_text_returns_empty():
    import src.agents.pyq.graph as g

    assert g.parse_questions("   ") == []
