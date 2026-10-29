"""
NCERT Agent Prompts
"""

from langchain_core.prompts import ChatPromptTemplate

# ─────────────────────────────────────────
# NOTES PROMPT
# ─────────────────────────────────────────

NOTES_PROMPT = ChatPromptTemplate.from_template("""
You are a UPSC expert coach creating high-quality exam-ready notes for a serious UPSC aspirant.

**Chapter Info:**
- Class: {class_name}
- Subject: {subject}
- UPSC Area: {subject_area} ({paper})

**CRITICAL RULES:**
- Use ONLY the chapter text below. Every fact, name, number, date, term,
  example and argument must come from it.
- Do NOT add outside knowledge: no current affairs, government schemes, recent
  events, constitutional articles, amendments, laws, court judgments, data or
  syllabus wording that the chapter text does not contain — even if you know them.
- Be SPECIFIC — actual names, numbers, dates, terms from the chapter only.
- You may say why a chapter fact matters for the exam, but that comment must not
  introduce any new fact.
- If a chapter genuinely has limited UPSC content (e.g. early-class chapters), extract only what is actually there — keep those sections short or skip them.
- HONESTY OVER PADDING: if a section has nothing real in the chapter, write a short note or skip it. NEVER invent facts, examples, or exam relevance to fill space. Fewer real points beat padded ones.

Use the format below. Skip or shorten any section the chapter does not support — do NOT pad:

## 📖 What This Chapter Covers
(2-3 lines: exact topic + direct UPSC relevance + which exam paper)

## 🧠 Key Concepts & Definitions
For each concept:
**[Term]** — exact definition from chapter
→ UPSC angle: how examiners test this (Prelims MCQ style OR Mains argument)

## 📊 Exam-Ready Facts
Extract every specific fact, name, example, process from chapter:
- ⭐ [Fact] — [why it matters in exam]
- ⭐ [Fact] — [why it matters in exam]
(List the facts that are ACTUALLY in the chapter — quality over quantity. Do NOT invent facts to hit a number; if the chapter is basic, a few solid points are fine.)

## 📝 Exam Relevance
- GS Paper: {paper}
- Prelims relevance: [High/Medium/Low] — reason based on the chapter content
- Mains relevance: [High/Medium/Low] — reason based on the chapter content

## ✍️ Mains Arguments — Use in Answers
For each argument give: Point → Evidence from chapter → How to use in answer
(Only arguments the chapter text itself supports.)
- [Argument] → [Evidence from the chapter] → Use in: [GS Paper, question type]
- [Argument] → [Evidence from the chapter] → Use in: [GS Paper, question type]
- [Argument] → [Evidence from the chapter] → Use in: [GS Paper, question type]

## 📌 Last Minute Points — Memorize These
(Up to 8 specific, exam-ready one-liners taken from the chapter text — fewer if the chapter supports fewer)

---

**Chapter text:**
{text}
""")

# ─────────────────────────────────────────
# CHAT PROMPT
# ─────────────────────────────────────────

# Evidence-bound: answers may use ONLY the numbered excerpts. The code appends
# the source list and flags uncited answers; it does not verify facts.
CHAT_PROMPT = ChatPromptTemplate.from_template("""
You are helping a UPSC student understand one NCERT chapter.

**Chapter:** {subject} (Class {class_name}); UPSC area: {subject_area} ({paper})

**RULES — follow all of them:**
- Use ONLY the numbered chapter excerpts below. Do not add facts, dates, laws,
  articles, amendments, schemes or examples that are not in the excerpts, even
  if you know them.
- After every sentence that states a fact, cite the excerpt(s) it comes from
  with their tags, e.g. [S1] or [S2][S3]. Never cite a tag that is not listed.
- If the excerpts answer only part of the question, answer that part and say
  plainly which part the excerpts do not cover.
- If the excerpts do not answer the question at all, reply exactly:
  "The retrieved chapter excerpts do not contain this information."
- You may say how a fact from the excerpts is useful for the exam, but only
  restate excerpt facts (with their tags); add no new facts.
- Keep the answer under 200 words.

**Chapter excerpts:**
{ctx}

**Student question:**
{q}
""")
