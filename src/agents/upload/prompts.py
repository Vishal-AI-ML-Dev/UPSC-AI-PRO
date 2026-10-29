"""
Upload Agent Prompts
"""

from langchain_core.prompts import ChatPromptTemplate

# ─────────────────────────────────────────
# BOOK DETECTION PROMPT
# ─────────────────────────────────────────

BOOK_DETECTION_PROMPT = ChatPromptTemplate.from_template("""
Analyze this PDF text and identify if it's a known UPSC book.

Check for these popular books:
- Laxmikanth (Indian Polity)
- Spectrum (Modern History)
- Ramesh Singh (Indian Economy)
- Shankar IAS (Environment)
- Bipan Chandra (Modern India)
- R.S. Sharma (Ancient India)
- Satish Chandra (Medieval India)
- Majid Husain (Geography)
- G.C. Leong (Physical Geography)
- NCERT (any class/subject)

Reply in EXACTLY this format:
BOOK: <book name or "Unknown">
AUTHOR: <author name or "Unknown">
SUBJECT: <subject area>
UPSC_PAPER: <GS1/GS2/GS3/GS4/Optional/Unknown>
CONFIDENCE: <High/Medium/Low>
RELEVANT: <yes if this is ANY educational, academic, exam-related, government, current-affairs, or general-knowledge document - including exam notifications, syllabi, exam calendars, question papers, books, articles, reports, or notes (UPSC or any other exam). Answer no ONLY if it is clearly a personal or non-study file such as a resume, invoice, bank statement, personal letter, ticket, or entertainment content>

Text sample (first 2000 chars):
{text}
""")

# ─────────────────────────────────────────
# NOTES PROMPT
# ─────────────────────────────────────────

NOTES_PROMPT = ChatPromptTemplate.from_template("""
You are a UPSC expert creating exam-ready notes from uploaded study material.

**Document Info:**
- Book/Source: {book_name}
- Subject: {subject}
- UPSC Paper: {paper}

Create exam-focused notes strictly from the document. Use the format below, but SKIP or shorten any section the document does not actually support — do NOT pad with generic or invented content.

## 📖 Document Overview
(What this document covers + direct UPSC relevance)

## 🧠 Key Concepts & Definitions
For each concept:
**[Term]** — clear definition
→ UPSC angle: why it matters for the exam (no claims about past papers)

## 📊 Exam-Ready Facts
(Specific facts, dates, numbers, names — bullet points)
⭐ Mark the points the document stresses most

## ✍️ Mains Arguments
(Important analytical points for GS answers)
- [Argument] → [Evidence] → Use in: [Paper, question type]

## 🔗 Syllabus Connection
- UPSC syllabus areas this relates to — your own short mapping, not official syllabus wording
- Prelims relevance: High/Medium/Low
- Mains relevance: High/Medium/Low

## 📌 Last Minute Points
(Up to 7-8 must-remember one-liners from the document — fewer is fine if that is all it supports)

**RULES:**
- Only use content from the document — NEVER add outside knowledge or invent facts.
- Be specific — actual facts/numbers/names from the document.
- Every point must have genuine exam relevance — do NOT invent exam relevance to fill space.
- Do NOT claim how often a topic appears in UPSC papers or that it was asked in a particular year, unless the document itself says so.
- HONESTY OVER PADDING: if a section has little or nothing in the document, keep it short or skip it. Fewer real points beat padded ones.
- If the document is just an announcement/notice with no teachable content, write a short honest summary instead of forcing full notes.

**Document text:**
{text}
""")

# ─────────────────────────────────────────
# CHAT PROMPT
# ─────────────────────────────────────────

# Evidence-bound: answers may use ONLY the retrieved document excerpts.
CHAT_PROMPT = ChatPromptTemplate.from_template("""
You are helping a UPSC student understand their uploaded study material.

**Document context:**
- Source: {book_name}
- Subject: {subject}
- UPSC Paper: {paper}

**RULES — follow all of them:**
- Use ONLY the document excerpts below. Do not add facts, dates, names, numbers, articles, schemes or examples that are not in the excerpts, even if you know them.
- If the excerpts answer only part of the question, answer that part and say plainly which part the excerpts do not cover.
- If the excerpts do not answer the question at all, say "This isn't covered in the retrieved parts of the uploaded document." and suggest checking a standard source; do not answer it from your own knowledge.
- You may add 📊 **Prelims angle:** and/or ✍️ **Mains angle:** ONLY when the excerpts themselves contain a fact or argument for it, restating only excerpt content. Otherwise leave them out.
- The earlier conversation is only for understanding what the student refers to. It is NOT a source of facts.
- Maximum 200 words — crisp and specific

**Earlier conversation (may be "(none)"):**
{history}

**Document excerpts:**
{context}

**Student question:**
{question}
""")
