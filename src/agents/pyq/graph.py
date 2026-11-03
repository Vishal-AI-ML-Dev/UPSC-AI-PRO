"""
PYQ Agent - AI question generation, smart parser, hints, explanations
"""

import json
import logging
import os
import re

from src.agents.pyq.prompts import (
    BANK_GEN_PROMPT,
    PARSER_PROMPT,
    QUESTION_GEN_PROMPT,
    VERIFY_PROMPT,
)
from src.core.llm import get_llm, message_text
from src.core.prompt_safety import harden_untrusted

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# UPSC-PATTERN EXEMPLARS (style anchors, NOT a data dump)
# A tiny curated set so generated questions feel like the real exam
# instead of generic quiz items. No big past-paper data needed.
# ─────────────────────────────────────────

_PRELIMS_EXEMPLARS = {
    "polity": "With reference to the Constitution of India, consider the following statements:\n1. ...\n2. ...\nWhich of the statements given above is/are correct?\n(a) 1 only (b) 2 only (c) Both 1 and 2 (d) Neither 1 nor 2",
    "history": "With reference to the Indian freedom struggle, consider the following events and arrange them in correct chronological order.",
    "geography": "Consider the following pairs (River : Tributary). How many of the above pairs are correctly matched?",
    "economy": "With reference to the Indian economy, consider the following statements. Which of the statements given above is/are correct?",
    "environment": "Consider the following species. Which of the above are found in India / are critically endangered?",
    "science": "With reference to recent developments in science and technology, consider the following statements. Which is/are correct?",
}

_MAINS_EXEMPLARS = {
    "polity": '"..." Critically examine in the context of Indian polity. (15 marks, 250 words)',
    "history": "To what extent did ... shape modern India? Discuss. (15 marks, 250 words)",
    "ethics": "What do you understand by '...'? Illustrate with examples from public life. (10 marks, 150 words)",
    "ir": '"India\'s neighbourhood-first policy faces structural challenges." Analyse. (15 marks, 250 words)',
}

_SUBJECT_KEYWORDS = {
    "polity": [
        "constitution",
        "right",
        "parliament",
        "judiciary",
        "amendment",
        "panchayat",
        "governance",
        "federal",
    ],
    "history": [
        "history",
        "revolt",
        "congress",
        "gandhi",
        "movement",
        "ancient",
        "medieval",
        "modern",
        "freedom",
    ],
    "geography": [
        "monsoon",
        "river",
        "soil",
        "climate",
        "mineral",
        "geography",
        "vegetation",
        "agriculture",
    ],
    "economy": [
        "bank",
        "monetary",
        "fiscal",
        "budget",
        "inflation",
        "economy",
        "gdp",
        "trade",
        "tax",
    ],
    "environment": [
        "biodiversity",
        "environment",
        "climate change",
        "pollution",
        "ecology",
        "wildlife",
        "species",
        "renewable",
    ],
    "science": [
        "space",
        "isro",
        "technology",
        "biotech",
        "nuclear",
        "science",
        " ai",
        "defence",
        "telecom",
    ],
    "ethics": ["ethic", "attitude", "integrity", "emotional", "values", "aptitude"],
    "ir": ["international", "bilateral", "neighbour", "foreign", "diplomacy", "relations"],
}


def _pick_exemplars(topic: str, question_type: str) -> str:
    """Return 1-2 UPSC-style exemplars matching the topic, as a style anchor."""
    t = (topic or "").lower()
    is_prelims = question_type.lower() in ("mcq", "prelims", "objective")
    table = _PRELIMS_EXEMPLARS if is_prelims else _MAINS_EXEMPLARS
    matched = [
        table[subj]
        for subj, kws in _SUBJECT_KEYWORDS.items()
        if subj in table and any(kw in t for kw in kws)
    ]
    if not matched:
        matched = list(table.values())[:2]
    return "\n\n".join(f"- {ex}" for ex in matched[:2])


def _self_check(raw_questions: str) -> str:
    """Second pass: fact-check generated questions, fix wrong answers / fabrications."""
    if not raw_questions or not raw_questions.strip():
        return raw_questions
    try:
        chain = VERIFY_PROMPT | get_llm()
        fixed = message_text(chain.invoke({"questions": raw_questions}).content).strip()
        # Only trust the verifier if it returned something substantial.
        if len(fixed) >= 0.5 * len(raw_questions.strip()):
            return fixed
        logger.warning("PYQ self-check returned too little; using original generation")
    except Exception as e:
        logger.warning(f"PYQ self-check pass failed ({e}); using original generation")
    return raw_questions


def generate_questions(
    topic: str,
    question_type: str = "mcq",
    difficulty: str = "medium",
    num_questions: int = 5,
    marks: int = 10,
):
    """Generate practice questions (streaming)."""
    if not topic or not topic.strip():
        yield "Please provide a topic to generate questions."
        return

    yield (
        "> ⚠️ AI-generated practice questions - always verify the answers against a standard source."
        + chr(10)
        + chr(10)
    )
    try:
        chain = QUESTION_GEN_PROMPT | get_llm()
        # Pass 1: generate (grounded on UPSC-style exemplars)
        raw = message_text(
            chain.invoke(
                {
                    "topic": topic.strip(),
                    "question_type": question_type,
                    "difficulty": difficulty,
                    "num_questions": num_questions,
                    "marks": marks,
                    "exemplars": _pick_exemplars(topic, question_type),
                }
            ).content
        )
        if not raw.strip():
            raise ValueError("model returned no text")
        # Pass 2: self-check / fact-verify before showing the student
        verified = _self_check(raw)
        # Stream out in line-sized chunks so the response still feels responsive
        for line in verified.splitlines(keepends=True):
            yield line
    except Exception as e:
        logger.error(f"Question generation failed: {e}")
        yield "Could not generate questions. Please try again."


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _first_json_blob(s: str):
    """Return the first balanced top-level JSON array/object substring, or None."""
    start = None
    for i, ch in enumerate(s):
        if ch in "[{":
            start = i
            break
    if start is None:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return s[start : j + 1]
    return None


def _extract_json_questions(raw: str):
    """Robustly pull a list of question dicts out of an LLM response."""
    if not raw or not raw.strip():
        return []
    cleaned = _strip_fences(raw)
    candidates = [cleaned]
    blob = _first_json_blob(cleaned)
    if blob and blob != cleaned:
        candidates.append(blob)
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("questions"), list):
                return [d for d in data["questions"] if isinstance(d, dict)]
            return [data]
    return []


_OPT_RE = re.compile(r"^\s*\(?([a-dA-D])[\)\.]\s*(.+?)\s*$")
_ANS_RE = re.compile(r"(?i)^\s*answer\s*[:\-]\s*(.+)$")
_EXP_RE = re.compile(r"(?i)^\s*explanation\s*[:\-]")
_QHEAD_RE = re.compile(r"(?im)^\s*(?:Q\s*\.?\s*)?\d{1,2}[\.\)]\s+")


def _split_blocks(text: str):
    """Split a multi-question blob into per-question blocks (Q1./1)/Q.1)."""
    parts = _QHEAD_RE.split(text)
    blocks = [p.strip() for p in parts if p and p.strip()]
    return blocks if blocks else [text.strip()]


def _parse_block(block: str):
    """Parse a single question block into a structured dict."""
    options = []
    q_lines = []
    answer = None
    for ln in block.splitlines():
        s = ln.strip()
        if not s:
            continue
        am = _ANS_RE.match(s)
        if am:
            answer = am.group(1).strip()
            continue
        if _EXP_RE.match(s):
            continue
        m = _OPT_RE.match(s)
        if m and len(m.group(2)) <= 200 and len(options) < 6:
            options.append(m.group(2).strip())
        elif not options:
            q_lines.append(s)
    question = " ".join(q_lines).strip() or block.strip()
    mm = re.search(r"(\d{1,3})\s*marks", block, re.IGNORECASE)
    marks = int(mm.group(1)) if mm else None
    if len(options) >= 2:
        return {
            "question": question,
            "type": "prelims",
            "options": options,
            "answer": answer,
            "marks": marks,
        }
    return {
        "question": question,
        "type": "mains",
        "options": None,
        "answer": answer,
        "marks": marks or 10,
    }


def _regex_parse(text: str):
    """Offline fallback: split into question blocks so options never merge."""
    blocks = _split_blocks(text)
    out = [_parse_block(b) for b in blocks]
    out = [q for q in out if q.get("question")]
    return out or [_parse_block(text)]


def _coerce_questions(items):
    """Normalise type/marks/subject so frontend branches work reliably."""
    out = []
    for q in items or []:
        if not isinstance(q, dict):
            continue
        qt = str(q.get("type", "")).strip().lower()
        opts = q.get("options")
        has_opts = isinstance(opts, list) and len(opts) >= 2
        if "main" in qt or "descriptive" in qt or "subjective" in qt:
            q["type"] = "mains"
        elif qt in ("mcq", "mcqs", "prelims", "objective") or has_opts:
            q["type"] = "prelims"
        else:
            q["type"] = "prelims" if has_opts else "mains"
        if q["type"] == "prelims" and isinstance(opts, list) and len(opts) > 6:
            q["options"] = opts[:4]
        m = q.get("marks")
        if isinstance(m, str):
            mm = re.search(r"\d+", m)
            q["marks"] = int(mm.group()) if mm else None
        if not q.get("subject") and q.get("topic"):
            q["subject"] = q.get("topic")
        q.setdefault("question", "")
        out.append(q)
    return out


def parse_questions(text: str) -> list[dict]:
    """Parse pasted question text into structured format (robust)."""
    if not text or not text.strip():
        return []
    raw = ""
    try:
        chain = PARSER_PROMPT | get_llm()
        # Pasted text is untrusted (Phase 07 F1): fence it before the parser prompt.
        pasted = harden_untrusted(text.strip(), label="pasted question text")
        raw = message_text(chain.invoke({"text": pasted}).content)
    except Exception as e:
        logger.error(f"Parser LLM call failed: {e}")
    questions = _coerce_questions(_extract_json_questions(raw))
    if questions:
        return questions
    logger.warning("PYQ parser: LLM JSON empty/unparsable, using regex fallback")
    return _coerce_questions(_regex_parse(text))


PRELIMS_TOPICS = [
    "Fundamental Rights",
    "Directive Principles",
    "Constitutional Amendments",
    "Parliament Procedures",
    "Judiciary System",
    "Panchayati Raj",
    "Constitutional Bodies",
    "Emergency Provisions",
    "Revolt of 1857",
    "Indian National Congress",
    "Gandhi Movements",
    "Revolutionary Movements",
    "Socio-Religious Reforms",
    "Post-Independence",
    "Monsoon System",
    "Indian Rivers",
    "Soil Types",
    "Natural Vegetation",
    "Mineral Resources",
    "Agriculture Patterns",
    "Climate Regions",
    "Banking System",
    "Monetary Policy",
    "Fiscal Policy",
    "Budget Concepts",
    "Inflation Types",
    "Balance of Payments",
    "Economic Reforms",
    "Biodiversity Hotspots",
    "Protected Areas",
    "Climate Change",
    "Environmental Conventions",
    "Pollution Control",
    "Renewable Energy",
    "Space Missions",
    "Defence Technology",
    "Biotechnology",
    "Nuclear Energy",
    "IT and Telecom",
    "Health and Disease",
]

MAINS_TOPICS = [
    "Indian Culture and Heritage",
    "Modern Indian History",
    "World History",
    "Indian Society",
    "Women and Population",
    "Urbanization",
    "Indian Constitution",
    "Governance Issues",
    "Social Justice",
    "International Relations",
    "India and Neighbours",
    "Bilateral Relations",
    "Indian Economy",
    "Agriculture Issues",
    "Infrastructure",
    "Science and Technology",
    "Environment and Ecology",
    "Disaster Management",
    "Internal Security",
    "Cyber Security",
    "Ethics Basics",
    "Attitude and Aptitude",
    "Civil Service Values",
    "Emotional Intelligence",
    "Case Studies",
    "Ethical Dilemmas",
]


def get_topic_suggestions(question_type: str = "mcq") -> list[str]:
    """Get topic suggestions based on question type."""
    if question_type.lower() in ["mcq", "prelims"]:
        return PRELIMS_TOPICS
    return MAINS_TOPICS


def process_question_batch(questions: list[dict]) -> list[dict]:
    """Add hints and analysis to a batch of questions."""
    results = []

    for q in questions:
        result = q.copy()

        if not result.get("topic"):
            result["topic"] = "General"

        if not result.get("paper"):
            result["paper"] = "Prelims" if result.get("type") in ("mcq", "prelims") else "Mains"

        results.append(result)

    return results


# ─────────────────────────────────────────
# PERSONAL PYQ BANK (per-user, grounded on the user's OWN uploaded papers)
# No big shared dataset needed - each user uploads their own past papers and
# the AI generates fresh practice questions grounded on that material.
# ─────────────────────────────────────────


def _bank_key(user_id: str) -> str:
    """Persist key for a user's personal PYQ bank (one bank per user)."""
    from src.core.vector_store import make_persist_key

    return make_persist_key("pyqbank", str(user_id or "anon"))


def build_question_bank(file_content: bytes, filename: str, user_id: str) -> dict:
    """Extract text from an uploaded PYQ PDF and add it to the user's personal bank."""
    from src.agents.upload.graph import extract_pdf_text
    from src.core.vector_store import get_text_splitter, upsert_documents

    text, pdf_hash = extract_pdf_text(file_content, filename)

    docs = get_text_splitter().create_documents([text])
    for d in docs:
        d.metadata = {
            "source_type": "pyq_bank",
            "source_title": filename,
            "filename": filename,
            "pdf_hash": pdf_hash,
        }

    # Append to the user's bank collection (auto-created on first upload).
    upsert_documents(_bank_key(user_id), docs, rebuild=False)
    logger.info(f"PYQ bank: added {len(docs)} chunks from {filename} (user={user_id})")

    approx_q = len(_QHEAD_RE.findall(text))
    return {
        "filename": filename,
        "hash": pdf_hash,
        "chunks": len(docs),
        "approx_questions": approx_q,
    }


def get_bank_status(user_id: str) -> dict:
    """Whether the user has a personal PYQ bank yet."""
    from src.core.vector_store import vector_store_exists

    return {"exists": vector_store_exists(_bank_key(user_id))}


def clear_bank(user_id: str) -> dict:
    """Delete the user's personal PYQ bank (Qdrant collection or local Chroma dir)."""
    from src.core.vector_store import _use_qdrant, collection_for, persist_dir_for

    key = _bank_key(user_id)
    if _use_qdrant():
        from src.core.vector_store import drop_qdrant_collection, get_qdrant_client

        client = get_qdrant_client()
        name = collection_for(key)
        existed = client.collection_exists(name)
        if existed:
            drop_qdrant_collection(client, name)  # also clears points in local mode
            logger.info(f"PYQ bank cleared (user={user_id}, collection={name})")
        return {"cleared": existed}
    import shutil

    persist_dir = persist_dir_for(key)
    existed = os.path.exists(persist_dir)
    if existed:
        shutil.rmtree(persist_dir, ignore_errors=True)
        logger.info(f"PYQ bank cleared (user={user_id})")
    return {"cleared": existed}


def _generate_grounded(
    persist_key: str,
    topic: str,
    question_type: str,
    num_questions: int,
    marks: int,
    *,
    label: str,
    banner: str,
    empty_msg: str,
    no_match_msg: str,
    open_fail_msg: str,
    fail_msg: str,
    default_query: str,
    default_topic: str,
    difficulty: str = "medium",
):
    """Shared grounded question generator (used by both the PYQ bank and lectures).

    Loads the given vector store, pulls relevant context, and streams self-checked
    questions built ONLY from that context - so output stays grounded on the source.
    """
    from src.core.vector_store import load_vector_store, similarity_search, vector_store_exists

    if not vector_store_exists(persist_key):
        yield "> \U0001f4ed " + empty_msg + chr(10)
        return

    try:
        db = load_vector_store(persist_key)
    except Exception as e:
        logger.error(f"Grounded gen load failed ({label}): {e}")
        yield "> \u26a0\ufe0f " + open_fail_msg + chr(10)
        return

    query = (topic or "").strip() or default_query
    ctx = similarity_search(db, query, k=8, label=label)
    if not ctx:
        yield "> \U0001f4ed " + no_match_msg + chr(10)
        return

    yield "> \u2705 " + banner + chr(10) + chr(10)
    try:
        chain = BANK_GEN_PROMPT | get_llm()
        raw = message_text(
            chain.invoke(
                {
                    # The user's own uploaded material is untrusted (Phase 07 F1).
                    "context": harden_untrusted(ctx, label=f"{label} excerpts"),
                    "topic": (topic or "").strip() or default_topic,
                    "question_type": question_type,
                    "num_questions": num_questions,
                    "marks": marks,
                    "difficulty": difficulty,
                }
            ).content
        )
        if not raw.strip():
            raise ValueError("model returned no text")
        verified = _self_check(raw)
        for line in verified.splitlines(keepends=True):
            yield line
    except Exception as e:
        logger.error(f"Grounded generation failed ({label}): {e}")
        yield fail_msg


def generate_from_bank(
    user_id: str,
    topic: str = "",
    question_type: str = "mcq",
    num_questions: int = 5,
    marks: int = 10,
    difficulty: str = "medium",
):
    """Generate practice questions GROUNDED on the user's own uploaded PYQ bank (streaming)."""
    yield from _generate_grounded(
        _bank_key(user_id),
        topic,
        question_type,
        num_questions,
        marks,
        label="pyqbank",
        banner=(
            "Based on YOUR uploaded question bank (its style and topics). The answers "
            "and explanations are AI-generated, not taken from your papers - always "
            "cross-check them with a standard source."
        ),
        empty_msg=(
            "You haven't uploaded any question paper yet. Upload a PYQ PDF to build "
            "your personal bank, then generate grounded practice questions."
        ),
        no_match_msg=(
            "Your bank doesn't seem to cover this topic. Try a different topic or "
            "upload more papers."
        ),
        open_fail_msg="Could not open your question bank. Please re-upload your PDF.",
        fail_msg="Could not generate questions from your bank. Please try again.",
        default_query="important previous year UPSC questions",
        default_topic="the uploaded papers",
        difficulty=difficulty,
    )
