"""Offline tests for the pure grounding / citation helpers.

Everything under test is dependency-free (src/core/grounding.py imports only
``re``), so this runs in CI with no API key, no langchain, no vector store -
the same offline-first contract as tests/test_eval_gate.py.

Also covers the two integration seams that must stay honest:
* the mentor ``web_search`` tool must fence live web text (harden_untrusted); and
* a hardened knowledge-base result must still yield a clean citation (its
  ``[UNTRUSTED ...]`` guard banner must NOT be mis-read as a source).
"""

from src.core.grounding import (
    MAX_CITATIONS,
    citations_from_tool_messages,
    compose_grounded_answer,
    derive_confidence,
    extract_web_citations,
    format_sources,
    format_trust_note,
    source_label,
)


# --------------------------------------------------------------------------- #
# source_label / format_sources
# --------------------------------------------------------------------------- #
def test_source_label_uses_metadata():
    label = source_label(
        {
            "score": 0.891,
            "metadata": {
                "source_type": "ncert",
                "source_title": "NCERT Class 6 Science - Chapter 1",
                "chunk_index": 3,
            },
        }
    )
    assert "NCERT Class 6 Science - Chapter 1" in label
    assert "type=ncert" in label
    assert "chunk=3" in label
    assert "score=0.891" in label


def test_source_label_falls_back_without_metadata():
    label = source_label({"score": 0.7, "metadata": {}})
    assert "retrieved context" in label
    assert "source" in label
    assert "score=0.7" in label


def test_format_sources_empty_returns_blank():
    assert format_sources([]) == ""
    assert format_sources(None) == ""
    assert format_sources([None, ""]) == ""


def test_format_sources_mixed_dict_and_string():
    text = format_sources(
        [{"metadata": {"source_title": "NCERT"}}, "web \u2014 https://upsc.gov.in"]
    )
    assert "Sources used:" in text
    assert "NCERT" in text
    assert "https://upsc.gov.in" in text
    assert "- 1." in text and "- 2." in text


def test_format_sources_caps_at_max():
    many = [f"web \u2014 https://e{i}.com" for i in range(10)]
    text = format_sources(many)
    assert text.count("- ") == MAX_CITATIONS


# --------------------------------------------------------------------------- #
# format_trust_note / derive_confidence
# --------------------------------------------------------------------------- #
def test_trust_note_silent_when_high_and_clean():
    assert format_trust_note("high", []) == ""


def test_trust_note_shows_low_confidence():
    note = format_trust_note("low", [])
    assert "grounding confidence is low" in note


def test_trust_note_lists_unsupported_claims_capped():
    note = format_trust_note("high", ["claimW", "claimX", "claimY", "claimZ"])
    assert "Potentially unsupported claim(s):" in note
    # capped at 3 - the 4th claim must be dropped
    assert "claimZ" not in note
    assert "claimW" in note and "claimX" in note and "claimY" in note


def test_derive_confidence():
    assert derive_confidence(grounded=True, relevant=True, has_evidence=True) == "high"
    assert derive_confidence(grounded=False, relevant=True, has_evidence=True) == "medium"
    assert derive_confidence(grounded=True, relevant=False, has_evidence=True) == "medium"
    assert derive_confidence(grounded=True, relevant=True, has_evidence=False) == "low"


# --------------------------------------------------------------------------- #
# extract_web_citations
# --------------------------------------------------------------------------- #
def test_extract_web_citations_parses_and_dedupes():
    digest = (
        "- Prelims 2026: notified (Source: https://upsc.gov.in/prelims)\n"
        "- Same again (Source: https://upsc.gov.in/prelims)\n"
        "- Result out (Source: https://pib.gov.in/x)"
    )
    out = extract_web_citations(digest)
    assert out == ["web \u2014 https://upsc.gov.in/prelims", "web \u2014 https://pib.gov.in/x"]


def test_extract_web_citations_empty():
    assert extract_web_citations("") == []
    assert extract_web_citations("no sources here") == []


# --------------------------------------------------------------------------- #
# citations_from_tool_messages (duck-typed; no langchain)
# --------------------------------------------------------------------------- #
class _FakeMsg:
    def __init__(self, type, name="", content=""):
        self.type = type
        self.name = name
        self.content = content


def test_citations_from_web_tool_message():
    msgs = [
        _FakeMsg("human", content="when is prelims"),
        _FakeMsg(
            "tool",
            name="web_search",
            content="- Prelims (Source: https://upsc.gov.in)",
        ),
    ]
    assert citations_from_tool_messages(msgs) == ["web \u2014 https://upsc.gov.in"]


def test_citations_from_hardened_kb_message_skips_guard_banner():
    # Simulate what knowledge_base_tool_fn now returns: a hardened passage whose
    # guard banner ALSO starts with "[" - it must NOT become a citation.
    from src.core.prompt_safety import harden_untrusted

    raw = "[NCERT Class 6 Science] Science begins with curiosity."
    hardened = harden_untrusted(raw, label="knowledge base excerpt")
    msgs = [_FakeMsg("tool", name="knowledge_base_search", content=hardened)]
    out = citations_from_tool_messages(msgs)
    assert out == ["knowledge base \u2014 NCERT Class 6 Science"]
    # the guard banner must never leak in as a fake source
    assert not any("UNTRUSTED" in c for c in out)


def test_citations_ignore_non_tool_messages():
    msgs = [_FakeMsg("ai", content="just talking (Source: https://x.com)")]
    assert citations_from_tool_messages(msgs) == []


def test_citations_empty_input():
    assert citations_from_tool_messages([]) == []
    assert citations_from_tool_messages(None) == []


# --------------------------------------------------------------------------- #
# compose_grounded_answer
# --------------------------------------------------------------------------- #
def test_compose_grounded_answer_full():
    text = compose_grounded_answer(
        "Answer body.",
        ["web \u2014 https://upsc.gov.in"],
        confidence="low",
        unsupported_claims=["exact date"],
    )
    assert text.startswith("Answer body.")
    assert "Sources used:" in text
    assert "grounding confidence is low" in text
    assert "exact date" in text


def test_compose_grounded_answer_high_confidence_no_note():
    text = compose_grounded_answer("Body.", [], confidence="high")
    assert text == "Body."


# --------------------------------------------------------------------------- #
# Integration seam: mentor web_search tool must fence live web text
# --------------------------------------------------------------------------- #
def test_web_search_tool_hardens_live_results(monkeypatch):
    import src.agents.mentor.graph as mentor_graph
    from src.core.prompt_safety import _FENCE_BEGIN, _FENCE_END

    monkeypatch.setattr(
        mentor_graph,
        "_fetch_search_context",
        lambda q: "- Prelims 2026 (Source: https://upsc.gov.in)",
    )
    from src.graph.tools import web_search_tool_fn

    out = web_search_tool_fn("prelims 2026 date")
    assert _FENCE_BEGIN in out and _FENCE_END in out
    # underlying content (incl. the citeable URL) is preserved verbatim
    assert "https://upsc.gov.in" in out
    # and citations can still be mined from the fenced digest
    assert extract_web_citations(out) == ["web \u2014 https://upsc.gov.in"]


def test_web_search_tool_no_results_is_not_fenced(monkeypatch):
    import src.agents.mentor.graph as mentor_graph
    from src.core.prompt_safety import _FENCE_BEGIN

    monkeypatch.setattr(mentor_graph, "_fetch_search_context", lambda q: "")
    from src.graph.tools import web_search_tool_fn

    out = web_search_tool_fn("prelims 2026 date")
    assert _FENCE_BEGIN not in out
    assert "No live web results" in out
