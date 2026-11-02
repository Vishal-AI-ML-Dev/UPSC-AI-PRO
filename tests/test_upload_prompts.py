"""Upload prompt contracts (UP1, UP3, UP4 of the 2026-10-29 prompt audit), mocked.

Prompt-wording tests check the instructions the model receives; they cannot
prove what a live model writes. Book-name tests run the real upload code with a
stand-in model that records the prompts (no PDF, store or network).
"""

import uuid

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableGenerator, RunnableLambda

import src.agents.upload.graph as ug
import src.core.vector_store as vector_store
from src.agents.upload.prompts import BOOK_DETECTION_PROMPT, CHAT_PROMPT, NOTES_PROMPT

DOC_TEXT = "Fundamental Rights are listed in Part III of the Constitution. " * 30
UNCONFIRMED = "Uploaded document (book not confirmed)"


def _chat_text():
    return CHAT_PROMPT.format(
        book_name="Doc",
        subject="Polity",
        paper="GS2",
        context="EXCERPT",
        question="Q?",
        history="(none)",
    )


def _notes_text():
    return NOTES_PROMPT.format(text="T", book_name="Doc", subject="Polity", paper="GS2")


# ----------------------------- UP1: chat ------------------------------------ #
def test_chat_prompt_is_evidence_bound():
    text = _chat_text()
    assert "For UPSC, what you should know" not in text
    assert "add UPSC angle" not in text
    assert "Use ONLY the document excerpts" in text
    assert "do not answer it from your own knowledge" in text


def test_chat_prompt_angles_are_conditional():
    text = _chat_text()
    assert "Always end with" not in text
    assert "ONLY when the excerpts themselves contain" in text


# ----------------------------- UP3: notes ----------------------------------- #
def test_notes_prompt_makes_no_pyq_frequency_claims():
    text = _notes_text()
    assert "high-frequency" not in text
    assert "how this is tested" not in text
    assert "how often a topic appears in UPSC papers" in text


def test_notes_prompt_makes_no_official_syllabus_claims():
    text = _notes_text()
    assert "Exact UPSC syllabus topics" not in text
    assert "not official syllabus wording" in text


def test_book_detection_allows_unknown_paper():
    assert "UPSC_PAPER: <GS1/GS2/GS3/GS4/Optional/Unknown>" in BOOK_DETECTION_PROMPT.format(
        text="T"
    )


# ----------------------------- UP4: guessed book name ----------------------- #
def _book(confidence):
    return (
        "BOOK: Indian Polity\nAUTHOR: M. Laxmikanth\nSUBJECT: Polity\n"
        f"UPSC_PAPER: GS2\nCONFIDENCE: {confidence}\nRELEVANT: yes"
    )


@pytest.fixture
def notes_prompt(monkeypatch):
    ug.clear_cache()
    monkeypatch.setattr(ug, "extract_pdf_text", lambda c, f: (DOC_TEXT, uuid.uuid4().hex))
    monkeypatch.setattr(ug, "create_vector_store", lambda text, **kw: None)

    def run(book_reply):
        seen, queue = [], [book_reply, "## Notes\n- point"]

        def reply(prompt_value):
            seen.append(prompt_value.to_string())
            return AIMessage(content=queue.pop(0))

        monkeypatch.setattr(ug, "get_llm", lambda: RunnableLambda(reply))
        ug.process_upload(b"%PDF", "my-notes.pdf", user_id="u1")
        return seen[1]  # the notes prompt

    return run


@pytest.mark.parametrize("confidence", ["Low", "Medium", "low - guessed from headings"])
def test_unconfirmed_book_is_not_named_as_source(notes_prompt, confidence):
    prompt = notes_prompt(_book(confidence))
    assert "Indian Polity" not in prompt
    assert UNCONFIRMED in prompt


def test_high_confidence_book_is_named(notes_prompt):
    prompt = notes_prompt(_book("High"))
    assert "Book/Source: Indian Polity" in prompt


def test_chat_does_not_name_unconfirmed_book(monkeypatch):
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(ug, "similarity_search", lambda db, q, k, label: "Excerpt")
    seen = []

    def stream(inputs):
        for value in inputs:
            seen.append(value.to_string())
        yield AIMessageChunk(content="Answer.")

    monkeypatch.setattr(ug, "get_llm", lambda: RunnableGenerator(stream))
    book_info = {"book": "Indian Polity", "confidence": "Low", "subject": "Polity", "paper": "GS2"}
    assert "".join(ug.ask_upload("What is Part III?", "h", user_id="u1", book_info=book_info)) == "Answer."
    assert "Indian Polity" not in seen[0]
    assert UNCONFIRMED in seen[0]
