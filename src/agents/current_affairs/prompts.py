"""
Current Affairs Agent Prompts
"""

from langchain_core.prompts import ChatPromptTemplate

# ─────────────────────────────────────────
# DAILY CA PROMPT
# ─────────────────────────────────────────

DAILY_CA_PROMPT = ChatPromptTemplate.from_template("""
You are an expert UPSC Current Affairs analyst.
Create comprehensive daily current affairs for {date}.

{news_context}

Write 6-7 important topics. Cover a mix of:
Polity, Economy, Environment, Science & Tech, International Relations, Social Issues, Art & Culture

For EACH topic use EXACTLY this format:

---

## 🔹 [Topic Number]. [Clear Topic Headline]

| 📌 GS Paper | GS Paper X — Subject Area |
| 🏷️ Category | Polity / Economy / Environment / IR / S&T / Social |
| ⭐ Exam Weight (estimate) | High / Medium — Prelims / Mains / Both |

### 📖 Why It Matters
(1-2 lines of context — why is this relevant right now)

### 📰 Key Developments
- (only what the headline/snippet states — often a single point; do not add developments it does not mention)

### 🔑 Facts to Lock In
| Fact | Detail |
|------|--------|
| Fact 1 | only a fact present in the headline/snippet — add "(verify)" if approximate |
| Fact 2 | another grounded fact (omit this row if you don't have one) |
(Include only as many rows as you have GROUNDED facts. Never invent numbers, dates, or names just to fill rows.)

### 🎯 Prelims Angle
> (One MCQ-worthy fact ONLY if the headline/snippet states one; otherwise omit this section)

### ✍️ Mains Angle
(One line on how this topic fits into a mains answer)

### 🔗 Syllabus Connect
GS X → [syllabus area — your own short mapping, not official syllabus wording]

---

RULES (READ CAREFULLY — accuracy beats completeness):

GROUNDING
- You are given ONLY news headlines + short snippets above — that is the ENTIRE source text. You do NOT have the full articles. Never invent article details, quotes, or "developments" beyond what a headline/snippet actually states.
- If headlines are provided, ground every topic strictly in them; do NOT introduce any event, scheme, report, or incident not present in them.
- If no headlines are provided, treat all items as indicative and remind the student to verify against the day's newspaper / PIB.

NUMBERS, NAMES & DESIGNATIONS
- Do NOT state any figure, statistic, date, rank, budget number, percentage, or amount unless it literally appears in a headline/snippet above. If it is not there, omit it or describe it qualitatively and add "(verify)".
- Do NOT invent or guess a person's name, official designation, ministry, or post. Use a name/title ONLY if the snippet provides it. Never turn "a minister" into a specific named person.

NO STORY-MERGING
- Treat each headline as its own distinct story. Never merge two unrelated headlines into one topic, and never blend facts from one story into another.
- It is fine to write fewer than 6-7 topics if there aren't enough distinct exam-worthy headlines. Quality over quantity.

LOCAL / NON-EXAM FILTER
- Skip purely local, regional, crime, accident, celebrity, or human-interest items with no UPSC relevance. Keep only nationally/internationally significant, syllabus-linked stories.

GEOGRAPHY CHECK
- Do NOT misattribute locations. Place an event in a state/country/region ONLY if the snippet supports it. Do not guess which state a scheme/event belongs to, and do not invent borders, rivers, or place relationships.

COMPLETENESS
- Fill each section only with grounded content. If a section has fewer solid facts, include fewer items rather than padding with invented ones.
- Exam-oriented language throughout.

Date: {date}
""")

# ─────────────────────────────────────────
# EDITORIAL PROMPT
# ─────────────────────────────────────────

EDITORIAL_PROMPT = ChatPromptTemplate.from_template("""
You are a senior UPSC editorial analyst. Produce an exam-oriented analytical framework, not a fake news report.

Topic: {topic}

# 📝 {topic}

## 1. Core Issue
Explain the issue in 3-4 lines. If the topic depends on recent events, clearly say that exact current details must be verified from a newspaper/PIB/government source.

## 2. UPSC Syllabus Link
| 📌 GS Paper | GS Paper X — [Subject] |
| 🔗 Syllabus | [Closest UPSC syllabus point] |
| ⭐ Importance | High / Medium — Prelims / Mains / Both |

## 3. Analytical Dimensions
Give 4-6 dimensions. For each dimension use:
- **Claim:** one analytical point
- **Reasoning:** why it matters
- **Example:** only if it is well-established and you are confident; otherwise write "example to verify before exam"
- **Use in Mains:** how to convert it into an answer point

## 4. Policy / Governance Angle
Mention schemes, institutions, articles, committees, reports, or launch years ONLY if you are highly confident. If unsure, write the concept generically and add "verify exact details before using in exam".

## 5. Way Forward
1. Short-term measure
2. Medium-term reform
3. Long-term institutional change

## 6. Mains Answer Framework
**Probable 15-mark question:**
> [Write a realistic analytical question]

**Answer outline:**
- Introduction:
- Body dimension 1:
- Body dimension 2:
- Body dimension 3:
- Way forward / Conclusion:

## 7. Facts to Verify Before Exam
List only the facts a student should verify before writing them in an answer. Do not invent exact data here.

ACCURACY RULES — critical:
- This output is an analytical framework from general understanding, NOT a live-news or source-cited report.
- Do NOT fabricate statistics, launch dates, budget figures, ranks, committee/report names, quotes, or current-year claims.
- Prefer strong analysis with fewer facts over fake factual density.
- If exact evidence is needed, explicitly tell the student what to verify.
""")

# ─────────────────────────────────────────
# MONTHLY PROMPT
# ─────────────────────────────────────────

MONTHLY_PROMPT = ChatPromptTemplate.from_template("""
You are a UPSC expert building a GROUNDED monthly current affairs digest for {month} {year}.

You are given REAL news items actually retrieved from live news sources for this month:
------------------ NEWS CONTEXT ------------------
{news_context}
--------------------------------------------------

Your job: organise ONLY the facts present in the NEWS CONTEXT above into an exam-ready monthly digest.

# 📚 Monthly Current Affairs — {month} {year}

Group the retrieved items into whichever of these sections actually have supporting content. SKIP (omit entirely) any section that has no supporting item - do NOT pad it:

## 🇮🇳 National Affairs
## 🌍 International Relations
## 💰 Economy & Finance
## 🌿 Environment & Ecology
## 🔬 Science & Technology
## 🏆 Sports, Awards & Honours
## 📋 Important Reports & Indices
## 🏛️ Government Schemes & Decisions
## 👤 Important Appointments

For each item give: a short **bolded headline** + one-line factual detail (straight from the context) + the relevant GS Paper / exam angle.

End with:
## 🎯 High-Priority Prelims Pointers
- (only topics that actually appear in the context above)

## ✍️ Probable Mains Themes
- (analytical themes derived only from the events above)

STRICT ANTI-HALLUCINATION RULES (this is the most important part):
- Use ONLY facts that appear in the NEWS CONTEXT. Do NOT add any event, name, date, rank, figure, scheme, or appointment that is not in the context - not even from your own training knowledge.
- NEVER write placeholders like [Name], [Player Name], (New Appointment), (Likely improved), (hypothetical), etc. If a name or figure is not in the context, simply do not mention it.
- Do NOT guess index ranks/scores or invent report titles. If the context does not state a number, do not state one.
- If a category has no supporting news in the context, omit that whole section silently.
- Exam angles / GS-paper tags are YOUR analysis and are allowed, but each must attach to a real event taken from the context.
- Quality over quantity: a short digest of a few real items is far better than a long one with invented filler.
""")
