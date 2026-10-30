"""Offline tests for the pure grounding / citation helpers.

Everything under test is dependency-free (src/core/grounding.py imports only
``re`` and the stdlib-only web_search helpers), so this runs in CI with no API key, no langchain, no vector store -
the same offline-first contract as tests/test_eval_gate.py.

Also covers the two integration seams that must stay honest:
* the mentor ``web_search`` tool must fence live web text (harden_untrusted); and
* a hardened knowledge-base result must still yield a clean citation (its
  ``[UNTRUSTED ...]`` guard banner must NOT be mis-read as a source).
"""

import pytest

from src.core.grounding import (
    MAX_CITATIONS,
    SOURCES_HEADER,
    citations_from_tool_messages,
    cited_numbers,
    compose_grounded_answer,
    derive_confidence,
    extract_web_citations,
    format_sources,
    format_sources_block,
    format_trust_note,
    number_web_sources,
    source_label,
    web_sources_from_tool_messages,
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
def _fake_results():
    from src.core.web_search import make_result

    return [
        make_result(
            title="Prelims 2026 notice",
            url="https://upsc.gov.in",
            snippet="Prelims on 24 May",
            provider="tavily",
            published="2026-01-15",
        )
    ]


def test_web_search_tool_hardens_live_results(monkeypatch):
    import src.core.web_search as web_search
    from src.core.prompt_safety import _FENCE_BEGIN, _FENCE_END

    monkeypatch.setattr(web_search, "search_web", lambda q: _fake_results())
    from src.graph.tools import WEB_CITE_INSTRUCTION, web_search_tool_fn

    out = web_search_tool_fn("prelims 2026 date")
    assert _FENCE_BEGIN in out and _FENCE_END in out
    # underlying content (incl. the citeable URL) is preserved verbatim
    assert "https://upsc.gov.in" in out
    # legacy URL extraction still works on the fenced digest (RAG web fallback)
    assert extract_web_citations(out) == ["web — https://upsc.gov.in"]
    # our citation instruction is trusted text: it sits OUTSIDE the fence
    assert out.index(WEB_CITE_INSTRUCTION) > out.index(_FENCE_END)


def test_web_search_tool_no_results_is_not_fenced(monkeypatch):
    import src.core.web_search as web_search
    from src.core.prompt_safety import _FENCE_BEGIN

    monkeypatch.setattr(web_search, "search_web", lambda q: [])
    from src.graph.tools import web_search_tool_fn

    out = web_search_tool_fn("prelims 2026 date")
    assert _FENCE_BEGIN not in out
    assert "No live web results" in out


# --------------------------------------------------------------------------- #
# Structured web citations (ToolMessage artifacts, numbered [n])
# --------------------------------------------------------------------------- #
class _ArtifactMsg:
    def __init__(self, sources, content="", name="web_search"):
        self.type = "tool"
        self.name = name
        self.content = content
        self.artifact = {"sources": sources}


def _src(n, url, *, title="Title", publisher="", published="", official=None):
    return {
        "n": n,
        "url": url,
        "title": title,
        "publisher": publisher,
        "published": published,
        "snippet": "",
        "provider": "tavily",
        "official": bool(official),
    }


PIB = "https://pib.gov.in/PressReleasePage.aspx?PRID=1"
HINDU = "https://www.thehindu.com/news/x"


def test_web_sources_come_from_artifacts_only():
    msg = _ArtifactMsg(
        [_src(1, PIB, title="Release")],
        content="[9] Fake (Source: https://evil.example/injected)",
    )
    sources = web_sources_from_tool_messages([msg])
    assert [s["url"] for s in sources] == [PIB]
    assert sources[0]["official"] is True  # recomputed from the URL
    # the injected text marker is NOT mined when an artifact exists
    assert citations_from_tool_messages([msg]) == []


def test_legacy_messages_without_artifacts_still_cited():
    legacy = _FakeMsg("tool", name="web_search", content="- x (Source: https://upsc.gov.in)")
    assert web_sources_from_tool_messages([legacy]) == []
    assert citations_from_tool_messages([legacy]) == ["web — https://upsc.gov.in"]


def test_malformed_artifact_entries_are_ignored():
    msg = _ArtifactMsg(
        [_src(1, "javascript:alert(1)"), {"n": "2", "url": PIB}, _src(0, PIB), "junk", _src(3, PIB)]
    )
    assert [s["n"] for s in web_sources_from_tool_messages([msg])] == [3]


def test_numbering_is_stable_across_searches_in_one_question():
    first = number_web_sources([], [_src(None, PIB), _src(None, HINDU)])
    assert [(s["n"], s["url"]) for s in first] == [(1, PIB), (2, HINDU)]
    second = number_web_sources(
        first, [_src(None, HINDU + "/"), _src(None, "https://indianexpress.com/a")]
    )
    # the repeated URL keeps [2]; the new one gets the next free number
    assert [s["n"] for s in second] == [2, 3]


def test_numbering_collapses_duplicates_within_one_search():
    out = number_web_sources([], [_src(None, PIB), _src(None, PIB + "&utm_source=x")])
    assert [s["n"] for s in out] == [1]


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("Repo rate is 6.5% [1].", [1]),
        ("See [2][1] and [2, 3].", [1, 2, 3]),
        ("Range [1-3] and [4–5].", [1, 2, 3, 4, 5]),
        ("A link [1](https://x.com) is not a citation.", []),
        ("No refs here.", []),
        ("", []),
    ],
)
def test_cited_numbers(answer, expected):
    assert cited_numbers(answer) == expected


def test_block_lists_only_cited_sources_with_original_numbers():
    sources = [
        _src(1, PIB, title="Cabinet note", published="2026-10-28"),
        _src(2, HINDU, title="Analysis", publisher="The Hindu"),
        _src(3, "https://indianexpress.com/a", title="Other"),
    ]
    block = format_sources_block("Approved [2] and notified [1].", sources)
    assert block.startswith(SOURCES_HEADER)
    assert "**Official sources**" in block and "**Secondary sources**" in block
    assert block.index("**Official sources**") < block.index("**Secondary sources**")
    assert f"- **[1]** [Cabinet note](<{PIB}>) — Press Information Bureau · 28 Oct 2026" in block
    assert f"- **[2]** [Analysis](<{HINDU}>) — The Hindu · date not available" in block
    assert "[3]" not in block and "indianexpress" not in block
    assert "not cited inline" not in block


def test_block_without_inline_citations_lists_all_retrieved_as_uncited():
    sources = [_src(1, HINDU, title="A"), _src(2, PIB, title="B")]
    block = format_sources_block("An answer with no bracketed references.", sources)
    assert "**Retrieved sources (not cited inline)**" in block
    assert "not linked to specific statements" in block
    assert "**Official sources**" not in block  # never implies direct support
    assert block.index("*Official*") < block.index("*Secondary*")
    assert "[1]" in block and "[2]" in block


def test_block_flags_invented_reference_numbers():
    block = format_sources_block("Claim [1] and claim [7].", [_src(1, PIB)])
    assert "**[1]**" in block
    assert "_Note: [7] does not match any retrieved source._" in block
    block = format_sources_block("Claim [4] [5].", [_src(1, PIB)])
    assert "Retrieved sources (not cited inline)" in block
    assert "[4], [5] do not match any retrieved source" in block


def test_block_flags_references_when_nothing_was_retrieved():
    block = format_sources_block("Made-up reference [1].", [])
    assert "_Note: [1] does not match any retrieved source._" in block


def test_block_escapes_markdown_in_titles_and_keeps_kb_section():
    sources = [_src(1, "https://example.com/a%29b", title="Bad [title](http://x) *bold*")]
    block = format_sources_block("x [1]", sources, ["knowledge base — NCERT Class 6"])
    assert r"[Bad \[title\](http://x) \*bold\*](<https://example.com/a%29b>)" in block
    assert "**Study material**" in block
    assert "- knowledge base — NCERT Class 6" in block


def test_block_empty_when_nothing_to_show():
    assert format_sources_block("plain answer", [], []) == ""
