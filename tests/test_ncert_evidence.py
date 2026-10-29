"""NCERT: auth before work, page-aware ingest metadata, evidence-bound chat.

All model calls are replaced by RunnableLambda stand-ins and the vector store by
recorders, so nothing here embeds, generates or writes an index. These tests
check plumbing and citation FORM; they cannot prove an answer is factually
supported by the cited text.
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableLambda

import src.agents.ncert.graph as ncert

STUDY = "/api/v1/ncert/study"
BODY = {"class_name": "class6", "subject": "socialscience", "chapter": "ch.pdf"}


# --------------------------------------------------------------------------- #
# 1. /ncert/study requires authentication before ANY work
# --------------------------------------------------------------------------- #
@pytest.fixture
def work_recorder(monkeypatch):
    """Make every unit of real work record itself (and fail loudly if reached)."""
    calls = []

    def _rec(name):
        def _f(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} must not run")

        return _f

    for name in ("read_chapter_pages", "get_llm", "upsert_documents", "vector_store_exists"):
        monkeypatch.setattr(ncert, name, _rec(name))
    return calls


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_study_rejects_unauthenticated_before_any_work(client, work_recorder, headers):
    r = client.post(STUDY, json=BODY, headers=headers)
    assert r.status_code == 401
    assert work_recorder == []  # no PDF read, model call, embedding check or index write


def test_study_authenticated_reaches_the_handler(client, make_user, monkeypatch):
    import src.api.routes.ncert as route

    seen = []
    monkeypatch.setattr(
        route,
        "generate_study_session",
        lambda **kw: (
            seen.append(kw)
            or {"notes": "n", "mindmap_html": "", "questions_html": "", "chapter_path": "p"}
        ),
    )
    u = make_user("ncert")
    r = client.post(STUDY, json=BODY, headers=u.headers)
    assert r.status_code == 200 and seen == [BODY]


# --------------------------------------------------------------------------- #
# 2. Page-aware ingest metadata through the real generate_study_session
# --------------------------------------------------------------------------- #
PAGE_1 = "Gram Sabha members discuss local matters. " * 5
PAGE_3 = "The Panchayati Raj system works at three levels. " * 60  # > chunk_size


@pytest.fixture
def ingest_env(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ncert,
        "read_chapter_pages",
        lambda c, s, ch: ([(1, PAGE_1), (3, PAGE_3)], "/x/class6/ss/ch.pdf"),
    )
    monkeypatch.setattr(
        ncert, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content="notes"))
    )
    import src.core.study_aids as aids

    monkeypatch.setattr(aids, "generate_study_aids", lambda *a, **k: ("", ""))
    monkeypatch.setattr(ncert, "vector_store_exists", lambda key: False)
    monkeypatch.setattr(
        ncert,
        "upsert_documents",
        lambda key, docs, rebuild=False: captured.update(key=key, docs=docs),
    )
    return captured


def test_ingest_keeps_document_page_and_chunk_metadata(ingest_env):
    ncert.generate_study_session("class6", "socialscience", "ch.pdf")
    docs = ingest_env["docs"]
    assert ingest_env["key"] == "ncert_v2_class6_socialscience_ch.pdf"
    assert len(docs) >= 3  # page 3 is longer than one chunk
    assert {d.metadata["pdf_page"] for d in docs} == {
        1,
        3,
    }  # physical PDF pages; page 2 had no text
    assert [d.metadata["chunk_index"] for d in docs] == list(range(len(docs)))
    for d in docs:
        md = d.metadata
        assert md["document"] == "ch.pdf" and md["source_type"] == "ncert"
        assert md["persist_key"] == ingest_env["key"]
        assert "book_page" not in md and "page" not in md  # printed page numbers are never guessed
        expected = PAGE_1 if md["pdf_page"] == 1 else PAGE_3
        assert d.page_content.strip() in expected  # chunk text comes from its own page only


def test_existing_index_is_reused_not_rewritten(ingest_env, monkeypatch):
    monkeypatch.setattr(ncert, "vector_store_exists", lambda key: True)
    ncert.generate_study_session("class6", "socialscience", "ch.pdf")
    assert "docs" not in ingest_env


def test_read_chapter_pages_uses_physical_page_numbers(monkeypatch, tmp_path):
    class _Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class _Reader:
        def __init__(self, path):
            self.pages = [_Page("one"), _Page("   "), _Page("three")]

    (tmp_path / "c" / "s").mkdir(parents=True)
    (tmp_path / "c" / "s" / "ch.pdf").write_bytes(b"%PDF-stub")
    monkeypatch.setattr(ncert, "BASE", str(tmp_path))
    monkeypatch.setattr(ncert, "PdfReader", _Reader)
    pages, _ = ncert.read_chapter_pages("c", "s", "ch.pdf")
    assert pages == [(1, "one"), (3, "three")]
    text, _ = ncert.read_chapter_pdf("c", "s", "ch.pdf")
    assert text == "one\nthree\n"


# --------------------------------------------------------------------------- #
# 3. Evidence-bound chat with inline references
# --------------------------------------------------------------------------- #
CHUNKS = [
    {
        "excerpt": "Gram Sabha: adults enrolled as voters.",
        "score": 0.86,
        "metadata": {"document": "ch.pdf", "pdf_page": 3, "chunk_index": 4},
    },
    {
        "excerpt": "Old chunk without page metadata.",
        "score": 0.80,
        "metadata": {"source_title": "NCERT class6 socialscience - ch.pdf", "chunk_index": 0},
    },
]


@pytest.fixture
def chat_env(monkeypatch):
    state = {
        "prompts": [],
        "reply": "The Gram Sabha is made of adult voters [S1].",
        "chunks": CHUNKS,
    }

    def _model(prompt_value):
        state["prompts"].append(prompt_value.to_string())
        for word in state["reply"].split(" "):
            yield AIMessageChunk(content=word + " ")

    monkeypatch.setattr(
        ncert, "load_vector_store", lambda key: state.setdefault("key", key) or object()
    )
    monkeypatch.setattr(
        ncert,
        "similarity_search_with_sources",
        lambda db, q, k=5, label="": {
            "chunks": state["chunks"],
            "context": "",
            "grounded": bool(state["chunks"]),
        },
    )
    monkeypatch.setattr(ncert, "get_llm", lambda: RunnableLambda(_model))
    return state


def _ask(q="What is the Gram Sabha?"):
    return "".join(ncert.ask_ncert(q, "class6", "socialscience", "ch.pdf"))


def test_chat_uses_page_aware_index_and_labels_sources(chat_env):
    out = _ask()
    assert chat_env["key"] == "ncert_v2_class6_socialscience_ch.pdf"
    prompt = chat_env["prompts"][0]
    assert "[S1] (ch.pdf, PDF p.3)" in prompt
    assert "[S2] (NCERT class6 socialscience - ch.pdf, page not recorded)" in prompt
    assert "Sources (retrieved chapter excerpts):" in out
    assert "- [S1] ch.pdf, PDF p.3" in out
    assert "[S2]" not in out.split("Sources")[1]  # only sources actually cited are listed
    assert "⚠️" not in out


def test_uncited_answer_is_flagged(chat_env):
    chat_env["reply"] = "The 73rd Amendment created the Gram Sabha."  # no citation tag
    out = _ask()
    assert "cites none of the retrieved chapter excerpts" in out
    assert "Sources (retrieved" not in out


def test_citation_to_unknown_source_does_not_count(chat_env):
    chat_env["reply"] = "Article 40 says so [S9]."
    out = _ask()
    assert "cites none of the retrieved chapter excerpts" in out
    assert "[S9]" not in out.split("⚠️")[1]  # the code-added footer never lists S9


def test_no_evidence_means_no_model_call(chat_env):
    chat_env["chunks"] = []
    out = _ask()
    assert out == ncert.NOT_IN_EXCERPTS
    assert chat_env["prompts"] == []


def test_explicit_not_covered_reply_is_not_flagged(chat_env):
    chat_env["reply"] = ncert.NOT_IN_EXCERPTS
    out = _ask()
    assert "⚠️" not in out and "Sources (retrieved" not in out


def test_chat_prompt_is_evidence_bound():
    template = ncert.CHAT_PROMPT.messages[0].prompt.template
    assert "Use ONLY the numbered chapter excerpts" in template
    assert "[S1]" in template
    assert "The retrieved chapter excerpts do not contain this information." in template
    # The old invitation to add outside UPSC facts and mandatory angles is gone.
    assert "For UPSC, what you should know" not in template
    assert "Prelims angle" not in template and "Mains angle" not in template


def test_notes_prompt_stays_within_the_chapter():
    template = ncert.NOTES_PROMPT.messages[0].prompt.template
    assert "Current Affairs" not in template
    assert "exact wording from UPSC syllabus" not in template
    assert "Use ONLY the chapter text below" in template
    assert "no current affairs, government schemes" in template
    assert "{text}" in template  # the chapter text is still supplied


def test_source_label_shows_pdf_page_only_when_recorded():
    from src.core.grounding import source_label

    with_page = source_label(
        {"metadata": {"source_title": "T", "source_type": "ncert", "pdf_page": 3}}
    )
    without = source_label({"metadata": {"source_title": "T", "source_type": "ncert"}})
    assert "PDF p.3" in with_page
    assert "p." not in without


# --------------------------------------------------------------------------- #
# Subject-area mapping: "science" must not match inside "socialscience"
# --------------------------------------------------------------------------- #
POLITY = ("Indian Polity & Governance", "GS Paper 2")
ECONOMY = ("Indian Economy", "GS Paper 3")
GEOGRAPHY = ("Geography", "GS Paper 1")
HISTORY = ("History / Indian Heritage & Culture", "GS Paper 1")
SOCIETY = ("Indian Society & Social Issues", "GS Paper 1")
SCI_TECH = ("Science & Technology", "GS Paper 3")
BROAD = ncert.SOCIAL_SCIENCE_AREA

# Every Social Science chapter currently in data/ncert, with its expected area.
SOCIAL_SCIENCE_CHAPTERS = {
    ("class6", "socialscience"): {
        "1-Introduction Why Social Science.pdf": BROAD,
        "2-Oceans and Continents.pdf": GEOGRAPHY,
        "3-Landforms and Life.pdf": GEOGRAPHY,
        "4-Timeline and Sources of History.pdf": HISTORY,
        "5-India, That Is Bharat.pdf": HISTORY,
        "6-The Beginnings of Indian Civilisation.pdf": HISTORY,
        "7-India’s Cultural Roots.pdf": HISTORY,
        "8-Unity in Diversity, or ‘Many in the One’.pdf": SOCIETY,
        "9-Family and Community.pdf": SOCIETY,
        "10-Grassroots Democracy – Part 1 Governance.pdf": POLITY,
        "11-Grassroots Democracy – Part 2 Local Government in Rural Areas.pdf": POLITY,
        "12-Grassroots Democracy – Part 3 Local Government in Urban Areas.pdf": POLITY,
        "13-The Value of Work.pdf": ECONOMY,
        "14-Economic Activities Around Us.pdf": ECONOMY,
    },
    ("class7", "socialscience(Part-1)"): {
        "1-Geographical Diversity of India.pdf": GEOGRAPHY,
        "2-Understanding the Weather.pdf": GEOGRAPHY,
        "3-Climates of India.pdf": GEOGRAPHY,
        "4-New Beginnings - Cities and States.pdf": HISTORY,
        "5-The Rise of Empires.pdf": HISTORY,
        "6-The Age of Reorganisation.pdf": HISTORY,
        "7-The Gupta Era An Age of Tireless Creativity.pdf": HISTORY,
        "8-How the Land Becomes Sacred.pdf": HISTORY,
        "9-From the Rulers to the Ruled - Types of Governments.pdf": POLITY,
        "10-The Constitution of India - An Introduction.pdf": POLITY,
        "11-From Barter to Money.pdf": ECONOMY,
        "12-Understanding Markets.pdf": ECONOMY,
    },
    ("class7", "socialscience(Part-2)"): {
        "1-The Story of Indian Farming.pdf": BROAD,
        "2-India and Her Neighbours.pdf": BROAD,
        "3-Empires and Kingdoms - 6th to 10th Centuries.pdf": HISTORY,
        "4-Turning Tides - 11th and 12th Centuries.pdf": HISTORY,
        "5-India, a Home to Many.pdf": SOCIETY,
        "6-The State, the Government, and You.pdf": POLITY,
        "7-Infrastructure Engine - Engine of India_s Development.pdf": ECONOMY,
        "8-Banks and the Magic of Finance.pdf": ECONOMY,
    },
}


@pytest.mark.parametrize(
    "class_name,subject,chapter,expected",
    [
        (c, s, ch, exp)
        for (c, s), chapters in SOCIAL_SCIENCE_CHAPTERS.items()
        for ch, exp in chapters.items()
    ],
)
def test_social_science_chapters_get_topic_area(class_name, subject, chapter, expected):
    area = ncert.detect_subject_area(subject, class_name, chapter)
    assert area == expected
    assert "Science & Technology" not in area[0]


@pytest.mark.parametrize(
    "subject", ["socialscience", "socialscience(Part-1)", "socialscience(Part-2)", "Social Science"]
)
def test_social_science_never_science_tech_and_broad_without_chapter(subject):
    assert ncert.detect_subject_area(subject, "class6") == BROAD


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("science", SCI_TECH),
        ("Science", SCI_TECH),
        ("history", HISTORY),
        ("Political Science", POLITY),
        ("biology", ("Science & Technology / Environment", "GS Paper 3")),
        ("sociology", ("Indian Society", "GS Paper 1")),
        ("sciencefiction", ncert.GENERAL_AREA),  # no substring matches
        ("music", ncert.GENERAL_AREA),
    ],
)
def test_other_subjects_match_whole_words(subject, expected):
    assert ncert.detect_subject_area(subject, "class6", "1-Anything.pdf") == expected


def test_science_chapters_stay_science_tech():
    for ch in ("1-The Wonderful World of Science.pdf", "12-Earth, Moon, and the Sun.pdf"):
        assert ncert.detect_subject_area("science", "class7", ch) == SCI_TECH


def test_every_local_social_science_chapter_is_covered():
    """Fail if data/ncert gains a Social Science chapter missing from the table."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "data" / "ncert"
    if not root.is_dir():
        pytest.skip("data/ncert not present")
    for subject_dir in root.glob("*/socialscience*"):
        key = (subject_dir.parent.name, subject_dir.name)
        assert sorted(p.name for p in subject_dir.glob("*.pdf")) == sorted(
            SOCIAL_SCIENCE_CHAPTERS[key]
        ), key
