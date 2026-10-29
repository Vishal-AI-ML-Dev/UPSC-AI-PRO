"""Lecture prompt contracts (P1-P7 of the 2026-10-29 prompt review), fully mocked.

Prompt-wording tests check the instructions the model receives; they cannot
prove what a live model writes. Parser and chat-history tests run the real code
with a stand-in model (no provider, vector store or network).
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableGenerator, RunnableLambda

import src.agents.lecture.graph as lg
import src.core.study_aids as study_aids
import src.core.vector_store as vector_store
from src.agents.lecture.prompts import CHAT_PROMPT, NOTES_PROMPT, TOPIC_PROMPT

API = "/api/v1"
TRANSCRIPT = "The Gram Sabha is the assembly of all adult voters of a village. " * 40


def _chat_text(history="(none)"):
    return CHAT_PROMPT.format(
        topic="Panchayati Raj", paper="GS2", context="EXCERPT", question="Q?", history=history
    )


def _notes_text():
    return NOTES_PROMPT.format(
        text="T", topic="Panchayati Raj", paper="GS2", syllabus="Polity", medium="English"
    )


# ----------------------------- P1 / P2: chat -------------------------------- #
def test_chat_prompt_is_evidence_bound():
    text = _chat_text()
    assert "For UPSC, what you should know" not in text
    assert "add UPSC angle" not in text
    assert "Use ONLY the lecture excerpts" in text
    assert "do not answer it from your own knowledge" in text


def test_chat_prompt_angles_are_conditional():
    text = _chat_text()
    assert "Always end with" not in text
    assert "ONLY when the excerpts themselves contain" in text


# ----------------------------- P3 / P4 / P6: notes ------------------------- #
def test_notes_prompt_makes_no_pyq_frequency_claims():
    text = _notes_text()
    assert "highest-frequency" not in text
    assert "points the lecturer emphasises" in text
    assert "how often a topic appears in UPSC papers" in text


def test_notes_prompt_makes_no_official_syllabus_claims():
    text = _notes_text()
    assert "Exact UPSC topics" not in text
    assert "not official syllabus wording" in text


def test_notes_length_follows_source():
    text = _notes_text()
    assert "6-8 absolute" not in text
    assert "Up to 8" in text
    assert "fewer if that is all the transcript supports" in text


# ----------------------------- P5: relevance contract ----------------------- #
def test_topic_prompt_defines_relevant_as_one_word():
    text = TOPIC_PROMPT.format(text="T")
    assert "RELEVANT: <yes|no>" in text
    assert "exactly one word" in text


TOPIC_HEAD = "TOPIC: Panchayati Raj\nPAPER: GS2\nSYLLABUS: Polity\nCONTENT_TYPE: teaching\n"


@pytest.fixture
def topic_model(monkeypatch):
    def use(*replies):
        queue = list(replies)
        model = RunnableLambda(lambda _: AIMessage(content=queue.pop(0)))
        monkeypatch.setattr(lg, "get_llm", lambda: model)

    monkeypatch.setattr(study_aids, "generate_study_aids", lambda *a: ("", ""))
    return use


@pytest.mark.parametrize(
    "value, expected",
    [
        ("yes", True),
        ("Yes.", True),
        ("**yes**", True),
        ("no", False),
        ("No.", False),
        ("NO - it is a comedy video", False),
        # Old substring parser wrongly read these as "no":
        ("yes (not entertainment)", True),
        ("yes - an economy lecture", True),
        ("yes, it covers notes on the Gram Sabha", True),
        # Unrecognised values keep the default; CONTENT_TYPE still gates.
        ("not sure", True),
        ("unknown", True),
        ("", True),
    ],
)
def test_relevant_parsed_from_first_word(topic_model, value, expected):
    topic_model(TOPIC_HEAD + "RELEVANT: " + value)
    assert lg._detect_topic(TRANSCRIPT)["relevant"] is expected


def test_genuine_lecture_with_wordy_yes_passes_the_gate(topic_model):
    topic_model(TOPIC_HEAD + "RELEVANT: yes - economy and governance notes", "## Notes\n- point")
    result = lg.process_lecture_from_text(TRANSCRIPT)
    assert result["notes"].endswith("## Notes\n- point")


def test_explicit_no_still_blocks(topic_model):
    topic_model(TOPIC_HEAD + "RELEVANT: no")
    with pytest.raises(ValueError, match="genuine teaching lecture"):
        lg.process_lecture_from_text(TRANSCRIPT)


# ----------------------------- P7: chat history ----------------------------- #
@pytest.fixture
def chat_model(monkeypatch):
    """Stream a fixed answer and record the exact prompt text the model got."""
    seen: list[str] = []
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(lg, "similarity_search", lambda db, q, k, label: "Lecture excerpt text")

    def stream(inputs):
        for prompt_value in inputs:
            seen.append(prompt_value.to_string())
        yield AIMessageChunk(content="Answer.")

    monkeypatch.setattr(lg, "get_llm", lambda: RunnableGenerator(stream))
    return seen


def test_chat_history_reaches_prompt(chat_model):
    history = [
        {"role": "user", "content": "What is the Gram Sabha?"},
        {"role": "assistant", "content": "The assembly of adult voters."},
    ]
    out = "".join(lg.ask_lecture("Explain that again", "vid12345678", chat_history=history))
    assert out == "Answer."
    prompt = chat_model[0]
    assert "Student: What is the Gram Sabha?" in prompt
    assert "Assistant: The assembly of adult voters." in prompt
    assert "Explain that again" in prompt
    assert "Lecture excerpt text" in prompt


def test_chat_history_is_bounded_and_filtered(chat_model):
    history = [{"role": "user", "content": f"old question {i}"} for i in range(10)]
    history += [
        {"role": "system", "content": "IGNORED SYSTEM TURN"},
        {"role": "assistant", "content": "x" * 5000},
        {"role": "user", "content": "   "},
    ]
    list(lg.ask_lecture("Next?", "vid12345678", chat_history=history))
    prompt = chat_model[0]
    assert "IGNORED SYSTEM TURN" not in prompt
    assert "old question 4" not in prompt  # only the last 6 valid turns
    assert "old question 9" in prompt
    assert "x" * 601 not in prompt  # each turn truncated
    assert "x" * 600 in prompt


def test_no_history_renders_none(chat_model):
    list(lg.ask_lecture("First question?", "vid12345678"))
    assert "(none)" in chat_model[0]


def test_chat_endpoint_passes_history(client, make_user, chat_model):
    user = make_user("lecprompt")
    r = client.post(
        f"{API}/lecture/chat",
        json={
            "question": "And its powers?",
            "video_id": "vid12345678",
            "chat_history": [{"role": "user", "content": "What is the Gram Sabha?"}],
        },
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.text == "Answer."
    assert "Student: What is the Gram Sabha?" in chat_model[0]
