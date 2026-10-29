"""
Lecture Agent Prompts
"""

from langchain_core.prompts import ChatPromptTemplate

# ─────────────────────────────────────────
# TRANSLATE PROMPT
# ─────────────────────────────────────────

TRANSLATE_PROMPT = ChatPromptTemplate.from_template("""
Translate the following text to clear, simple English.

- Keep all proper nouns, names, and technical terms unchanged
- Do not summarize — translate everything
- If already in English, return as-is

Text:
{text}
""")

# ─────────────────────────────────────────
# TOPIC DETECTION PROMPT
# ─────────────────────────────────────────

TOPIC_PROMPT = ChatPromptTemplate.from_template("""
Read this lecture transcript and identify:
1. Main topic (e.g. "Fundamental Rights", "Monsoon System")
2. UPSC GS Paper (GS1 / GS2 / GS3 / GS4 / Optional / Current Affairs)
3. Syllabus section (e.g. "Indian Polity", "Physical Geography")
4. Key subtopics covered (3-5 items)
5. CONTENT_TYPE - classify the video's MAIN PURPOSE into EXACTLY ONE of these:
   - teaching      = genuinely explains/teaches academic or exam-syllabus content (concepts, theory, facts, current-affairs analysis). A real lecture a student learns from.
   - entertainment = comedy, stand-up, skit, roast, satire, parody, reaction, vlog, podcast, casual chat/storytelling, OR a humorous/anecdotal take ABOUT an exam or topic.
   - motivational  = inspiration, success story, "how I cracked it", pep talk, with little or no actual subject teaching.
   - news_update   = only reports WHAT/WHEN (exam notification, result/admit-card/syllabus/book-release dates, announcements) WITHOUT teaching the subject.
   - other         = anything else or unclear.

CRITICAL: Judge by what the speaker is actually DOING, not by the keywords. A comedian narrating their funny UPSC-prep journey - full of jokes and anecdotes - is "entertainment", NOT teaching, EVEN THOUGH it mentions UPSC, Prelims, Mains, coaching, etc. Choose "teaching" ONLY when the video actually explains the subject so a student learns it.

Examples:
- A teacher explaining the Preamble and Fundamental Rights -> teaching
- A standup comedian joking about UPSC attempts, momos in Mukherjee Nagar, and filling the OMR sheet -> entertainment
- A topper sharing their daily routine and emotional journey with no concept teaching -> motivational
- A channel announcing the UPSC 2026 notification date -> news_update

6. RELEVANT - "yes" if the video teaches content from the UPSC Civil Services syllabus (any GS paper, an optional subject, or current-affairs analysis); "no" otherwise. Write exactly one word: yes or no.

The transcript may be in Hindi or English. Always answer in English.

Reply in EXACTLY this format (no extra text):
TOPIC: <main topic>
PAPER: <GS paper or Not Applicable>
SYLLABUS: <syllabus section>
SUBTOPICS: <comma-separated list>
CONTENT_TYPE: <teaching | entertainment | motivational | news_update | other>
RELEVANT: <yes|no>

Transcript (first 4000 chars):
{text}
""")

# ─────────────────────────────────────────
# NOTES PROMPT
# ─────────────────────────────────────────

NOTES_PROMPT = ChatPromptTemplate.from_template("""
You are an expert UPSC teacher creating exam-ready notes from a video lecture.

**Lecture details:**
- Topic: {topic}
- UPSC Paper: {paper}
- Syllabus area: {syllabus}

Create structured, exam-ready notes. Format EXACTLY as below:

## 📌 Lecture Overview
(2-3 sentence summary — what was taught and why it matters for UPSC)

## 🧠 Key Concepts Explained
(Main ideas explained simply — each concept as a clear bullet)

## 📊 Prelim Facts — MCQ Ready
(Specific facts, dates, numbers, names — bullet points)
⭐ Mark the points the lecturer emphasises most

## ✍️ Mains Keywords & Arguments
(Important terms + analytical angles for GS answers)

## 📚 Real Examples from Lecture
(Specific examples, case studies, comparisons mentioned)

## 🔗 Syllabus Connection
(UPSC syllabus areas this lecture relates to — your own short mapping, not official syllabus wording)

## 📌 Last Minute Revision — Must Know
(Up to 8 must-remember points from the transcript — fewer if that is all the transcript supports)

**RULES:**
- The transcript may be in Hindi, English, or mixed. Write the notes in the requested OUTPUT LANGUAGE: {medium}.
    - English: write everything in clear English.
    - Hindi: write in simple Hindi (Devanagari); keep widely-used technical/proper terms in English.
    - Hinglish: write in simple Hindi using Roman/Latin script, keeping UPSC technical terms in English.
- Keep the section HEADINGS (the ## lines with emojis) exactly as given, in English
- CRITICAL - NO HALLUCINATION: Use ONLY facts, dates, numbers, names and examples that are EXPLICITLY stated in the transcript. NEVER invent, guess or pad.
- Do NOT add generic textbook knowledge or made-up 'exam relevance'. If it is not in the transcript, do not write it.
- Do NOT claim how often a topic appears in UPSC papers or that it was asked in a particular year, unless the transcript itself says so.
- If a section has no real content from this video, write a single line: "Not covered in this video." Do NOT fill it with filler.
- If the video is only an announcement/news/update with no actual teaching, do NOT fabricate notes - give a short honest summary of what was said and state that it is an update, not a teaching lecture.
- Only use content from the lecture
- Be specific — include actual facts/numbers/names
- Ignore filler, intros, ads — only exam content

**Lecture transcript:**
{text}
""")

# ─────────────────────────────────────────
# CHAT PROMPT
# ─────────────────────────────────────────

# Evidence-bound: answers may use ONLY the retrieved lecture excerpts. The earlier
# conversation only helps resolve follow-ups ("explain that again"); it is not a
# fact source.
CHAT_PROMPT = ChatPromptTemplate.from_template("""
You are helping a UPSC student understand a lecture they just watched. The lecture excerpts may be in Hindi or English; always reply in English.

**Lecture context:**
- Topic: {topic}
- UPSC Paper: {paper}

**RULES — follow all of them:**
- Use ONLY the lecture excerpts below. Do not add facts, dates, names, numbers, articles, schemes or examples that are not in the excerpts, even if you know them.
- If the excerpts answer only part of the question, answer that part and say plainly which part the excerpts do not cover.
- If the excerpts do not answer the question at all, say "This wasn't covered in the retrieved parts of the lecture." and suggest checking a standard source; do not answer it from your own knowledge.
- You may add 📊 **Prelims angle:** and/or ✍️ **Mains angle:** ONLY when the excerpts themselves contain a fact or argument for it, restating only excerpt content. Otherwise leave them out.
- The earlier conversation is only for understanding what the student refers to. It is NOT a source of facts.
- Maximum 200 words — crisp and specific

**Earlier conversation (may be "(none)"):**
{history}

**Lecture excerpts:**
{context}

**Student question:**
{question}
""")
