"""Upload model-text handling and relevance parsing, fully mocked.

Gemini may return ``AIMessage.content`` as a list of blocks. Book detection,
notes and the streamed document chat must use the text, show the existing
failure messages when no text arrives, and the RELEVANT field must be read from
its first word (the old ``"no" not in value`` check rejected "yes - exam
notification"). No provider, PDF, vector store or network is touched.
"""

import uuid

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableGenerator, RunnableLambda

import src.agents.upload.graph as ug
import src.core.vector_store as vector_store

API = "/api/v1"
THOUGHT = "PRIVATE-THINKING-TEXT"
DOC_TEXT = "Panchayati Raj has three tiers: village, block and district. " * 30
BOOK_HEAD = "BOOK: Unknown\nAUTHOR: Unknown\nSUBJECT: Polity\nUPSC_PAPER: GS2\nCONFIDENCE: Low\n"
BOOK_TEXT = BOOK_HEAD + "RELEVANT: yes"
NOTES_TEXT = "## 📖 Document Overview\n- Three tiers of Panchayati Raj."
NOTES_FAIL = "⚠️ Notes generation failed. Please retry."
CHAT_FAIL = "⚠️ Could not process your question. Please try again."


def blocks(text):
    return [{"type": "thinking", "thinking": THOUGHT}, {"type": "text", "text": text}]


@pytest.fixture
def invoke_model(monkeypatch):
    """Queued replies (book detection, then notes); fake PDF + store; fresh cache."""
    monkeypatch.setattr(ug, "_processed_cache", {})
    monkeypatch.setattr(
        ug, "extract_pdf_text", lambda content, filename: (DOC_TEXT, uuid.uuid4().hex)
    )
    stores = []
    monkeypatch.setattr(ug, "create_vector_store", lambda text, **kw: stores.append(kw))

    def use(*contents):
        queue = list(contents)
        model = RunnableLambda(lambda _: AIMessage(content=queue.pop(0)))
        monkeypatch.setattr(ug, "get_llm", lambda: model)
        return stores

    return use


@pytest.fixture
def stream_model(monkeypatch):
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(ug, "similarity_search", lambda db, q, k, label: "Document excerpt")

    def use(contents):
        def stream(inputs):
            for _ in inputs:
                pass
            for content in contents:
                yield AIMessageChunk(content=content)

        monkeypatch.setattr(ug, "get_llm", lambda: RunnableGenerator(stream))

    return use


# ----------------------------- book detection -------------------------------- #
def test_detect_book_parses_list_content(invoke_model):
    invoke_model(blocks(BOOK_HEAD.replace("Unknown", "Indian Polity", 1) + "RELEVANT: yes"))
    info = ug.detect_book(DOC_TEXT)
    assert info["book"] == "Indian Polity"
    assert info["paper"] == "GS2"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("yes", True),
        ("no", False),
        ("No.", False),
        ("**no** - it is an invoice", False),
        # The old substring check read these as "no":
        ("yes - exam notification", True),
        ("yes (study notes)", True),
        ("yes, general-knowledge document on the economy", True),
        # Unrecognised -> existing default (relevant).
        ("unknown", True),
    ],
)
def test_detect_book_relevant_first_word(invoke_model, value, expected):
    invoke_model(BOOK_HEAD + "RELEVANT: " + value)
    assert ug.detect_book(DOC_TEXT)["relevant"] is expected


def test_study_notification_is_not_rejected(invoke_model):
    invoke_model(BOOK_HEAD + "RELEVANT: yes - UPSC exam notification", NOTES_TEXT)
    result = ug.process_upload(b"%PDF", "notice.pdf")
    assert result["notes"].endswith(NOTES_TEXT)


def test_explicit_no_still_rejects(invoke_model):
    invoke_model(BOOK_HEAD + "RELEVANT: no")
    with pytest.raises(ValueError, match="doesn.t look like study material"):
        ug.process_upload(b"%PDF", "invoice.pdf")


# ----------------------------- notes ---------------------------------------- #
def test_notes_list_content_becomes_text(invoke_model):
    stores = invoke_model(blocks(BOOK_TEXT), blocks(NOTES_TEXT))
    notes = ug.process_upload(b"%PDF", "polity.pdf")["notes"]
    assert isinstance(notes, str)
    assert notes.startswith("> ⚠️ AI-generated notes")
    assert notes.endswith(NOTES_TEXT)
    assert THOUGHT not in notes
    assert len(stores) == 1


def test_notes_string_unchanged(invoke_model):
    invoke_model(BOOK_TEXT, NOTES_TEXT)
    assert ug.process_upload(b"%PDF", "polity.pdf")["notes"].endswith(NOTES_TEXT)


@pytest.mark.parametrize(
    "content",
    [[], "", "   ", [{"type": "thinking", "thinking": THOUGHT}]],
    ids=["empty-list", "empty-string", "blank", "thinking-only"],
)
def test_notes_without_text_show_failure(invoke_model, content):
    invoke_model(BOOK_TEXT, content)
    assert ug.process_upload(b"%PDF", "polity.pdf")["notes"] == NOTES_FAIL


# ----------------------------- chat ----------------------------------------- #
LIST_STREAM = [
    [{"type": "text", "text": "Three tiers: "}],
    [{"type": "thinking", "thinking": THOUGHT}],
    [{"type": "text", "text": "village, block, district."}],
]
CHAT_TEXT = "Three tiers: village, block, district."


def test_chat_list_stream_yields_text(stream_model):
    stream_model(LIST_STREAM)
    chunks = list(ug.ask_upload("How many tiers?", "h"))
    assert all(isinstance(c, str) and c for c in chunks)
    assert "".join(chunks) == CHAT_TEXT


def test_chat_string_stream_unchanged(stream_model):
    stream_model(["Three tiers: ", "village, block, district."])
    assert "".join(ug.ask_upload("How many tiers?", "h")) == CHAT_TEXT


@pytest.mark.parametrize(
    "contents",
    [[], [""], [[]], [[{"type": "thinking", "thinking": THOUGHT}]]],
    ids=["no-chunks", "empty-string", "empty-list", "thinking-only"],
)
def test_chat_without_text_shows_failure(stream_model, contents):
    stream_model(contents)
    chunks = list(ug.ask_upload("How many tiers?", "h"))
    assert all(isinstance(c, str) for c in chunks)
    assert "".join(chunks) == CHAT_FAIL


def test_chat_endpoint_streams_text(client, make_user, stream_model):
    stream_model(LIST_STREAM)
    user = make_user("upload")
    r = client.post(
        f"{API}/upload/chat?pdf_hash=h",
        json={"request": {"question": "How many tiers?"}},
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.text == CHAT_TEXT
