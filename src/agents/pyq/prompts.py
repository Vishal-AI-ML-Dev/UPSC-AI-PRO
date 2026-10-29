"""
PYQ Agent Prompts
"""

from langchain_core.prompts import ChatPromptTemplate

QUESTION_GEN_PROMPT = ChatPromptTemplate.from_template("""
You are a UPSC question paper setter creating practice questions.

Topic: {topic}
Question Type: {question_type}
Difficulty: {difficulty}
Number of Questions: {num_questions}

Study these REAL UPSC-style patterns to match the exam's tone, framing and trickiness. Do NOT copy them - only mirror the style and difficulty:
{exemplars}

Generate exactly {num_questions} questions. Format based on type:

For Prelims MCQ:
Q1. [Question text]
(a) Option A
(b) Option B
(c) Option C
(d) Option D

Answer: (correct option)
Explanation: [Why this is correct + why others are wrong]

For Mains:
Q1. [Question text] ({marks} marks)

Model Answer Framework:
- Introduction: [1-2 lines]
- Body Points: [3-4 key arguments]
- Conclusion: [1 line]

Keywords to include: [5-6 important terms]

RULES:
- Questions must be UPSC-standard
- Cover different dimensions of the topic
- Prelims: factual, tricky options
- Mains: analytical, multi-dimensional

ACCURACY (critical):
- The option you mark as correct MUST be factually correct, and the explanation must be accurate. A wrong 'correct answer' is worse than no question.
- Do NOT invent fake statistics, fake committee/report names, or made-up data. Use only well-established facts.
- Do NOT claim a question "appeared in UPSC <year>" or attribute it to any real past paper - these are practice questions only.
- Each question must test a DISTINCT sub-topic; no two questions should overlap in concept.
- If you are not confident about a fact, build the question around a different, well-established aspect of the topic instead.

Generate now:
""")


PARSER_PROMPT = ChatPromptTemplate.from_template("""
Parse pasted UPSC question text into valid JSON.

Return ONLY a JSON array. No markdown, no commentary, no code fence.

Schema (an array of objects; `|` separates the allowed values):
[
  {{
    "question": "full question text",
    "type": "prelims" | "mains",
    "options": ["option a", "option b", "option c", "option d"] | null,
    "answer": "correct option or answer text" | null,
    "explanation": "given explanation if present" | null,
    "marks": 10 | 15 | null,
    "topic": "detected topic or null",
    "paper": "GS1" | "GS2" | "GS3" | "GS4" | "Essay" | "Optional" | "Prelims" | "Unknown"
  }}
]

RULES:
- Extract ALL questions from the pasted text.
- Preserve the original meaning; clean only formatting noise.
- If an answer or explanation is not provided, set it to null.
- For MCQs, options must be an array. For mains, options must be null.
- Return valid JSON only.

Pasted text:
{text}
""")


VERIFY_PROMPT = ChatPromptTemplate.from_template("""
You are a senior UPSC fact-checker reviewing AI-generated practice questions BEFORE they reach a student. Accuracy matters far more than quantity.

Generated questions:
{questions}

Do the following silently and return ONLY the cleaned questions in the EXACT same format you received (no commentary, no preamble):
1. For every MCQ, verify the marked "Answer" is factually correct. If it is wrong, correct the Answer and fix the explanation.
2. Delete or rewrite any question containing fabricated data, fake committee/report names, made-up statistics, or any "appeared in UPSC <year>" claim.
3. Make sure no two questions test the same fact/concept - replace duplicates with a distinct, well-established question on the same topic.
4. If a question cannot be made factually safe, replace it with a correct, well-established one on the same topic.

Return the cleaned questions now:
""")


# ─────────────────────────────────────────
# PYQ BANK PROMPT (grounded on the user's own uploaded papers)
# ─────────────────────────────────────────

BANK_GEN_PROMPT = ChatPromptTemplate.from_template("""
You are a UPSC question paper setter. The student has uploaded their OWN collection of previous-year question papers. Below are REAL excerpts from THOSE papers.

Previous-year question excerpts (the student's own material):
{context}

Using the style, difficulty and themes of the REAL questions above, create {num_questions} fresh practice questions on: {topic}
Question type: {question_type}  (mcq = Prelims MCQ with 4 options; mains = descriptive, {marks} marks)
Difficulty: {difficulty}

RULES:
- Mirror the framing, tone and trickiness of the REAL excerpts above. Stay close to topics that actually appear in them.
- These are practice questions INSPIRED by the uploaded papers - do NOT claim they are the exact original questions.
- For Prelims MCQ:
Q1. [Question text]
(a) Option A
(b) Option B
(c) Option C
(d) Option D

Answer: (correct option)
Explanation: [why correct + why others are wrong]
- For Mains:
Q1. [Question text] ({marks} marks)

Model Answer Framework:
- Introduction: [1-2 lines]
- Body Points: [3-4 key arguments]
- Conclusion: [1 line]

Keywords to include: [5-6 important terms]

ACCURACY (critical):
- The option you mark as correct MUST be factually correct.
- Do NOT invent fake statistics, fake committee/report names, or made-up data. Use only well-established facts.
- If the uploaded excerpts do not cover the requested topic well, build questions around the themes that ARE present instead.

Generate now:
""")
