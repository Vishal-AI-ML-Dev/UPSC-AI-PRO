"""Smoke: lecture relevance gate rejects non-teaching content (network mocked).

We never hit YouTube. We mock get_transcript (so there is enough text to reach
the gate) and _detect_topic (to simulate the classifier's verdict), then assert
that process_lecture raises ValueError for clearly non-teaching content. This
exercises the REAL gate logic inside process_lecture.
"""

import pytest

VALID_URL = "https://www.youtube.com/watch?v=abcdefghijk"
# > 2000 chars so we get past the "too short" content floor and reach the gate.
LONG_TRANSCRIPT = "This lecture explains the topic in detail. " * 120


def _patch_transcript(monkeypatch, lg):
    monkeypatch.setattr(lg, "get_transcript", lambda video_id: (LONG_TRANSCRIPT, "en"))


def _topic(**override):
    base = {
        "topic": "Some Topic",
        "paper": "GS2",
        "syllabus": "Polity",
        "subtopics": [],
        "content_type": "teaching",
        "relevant": True,
    }
    base.update(override)
    return base


def test_gate_blocks_entertainment(monkeypatch):
    import src.agents.lecture.graph as lg

    _patch_transcript(monkeypatch, lg)
    monkeypatch.setattr(
        lg,
        "_detect_topic",
        lambda text: _topic(content_type="entertainment", relevant=False),
    )
    with pytest.raises(ValueError):
        lg.process_lecture(VALID_URL)


def test_gate_blocks_multi_paper_overview(monkeypatch):
    import src.agents.lecture.graph as lg

    _patch_transcript(monkeypatch, lg)
    monkeypatch.setattr(lg, "_detect_topic", lambda text: _topic(paper="GS1, GS2, GS3, GS4"))
    with pytest.raises(ValueError):
        lg.process_lecture(VALID_URL)


def test_gate_blocks_not_applicable_paper(monkeypatch):
    import src.agents.lecture.graph as lg

    _patch_transcript(monkeypatch, lg)
    monkeypatch.setattr(lg, "_detect_topic", lambda text: _topic(paper="Not Applicable"))
    with pytest.raises(ValueError):
        lg.process_lecture(VALID_URL)


def test_invalid_youtube_url_rejected():
    import src.agents.lecture.graph as lg

    with pytest.raises(ValueError):
        lg.process_lecture("https://example.com/not-a-video")
