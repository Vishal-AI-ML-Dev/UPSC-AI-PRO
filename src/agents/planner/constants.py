"""
Planner Agent Constants - Attempt guidance, Optional contexts, Weak area contexts
"""

# ─────────────────────────────────────────
# ATTEMPT GUIDANCE
# ─────────────────────────────────────────

ATTEMPT_GUIDANCE = {
    "1": """
First attempt. Foundation is everything right now.
- NCERTs before any standard book — no exceptions
- Don't skip basics to seem advanced
- Syllabus coverage > answer writing at this stage
- Too many resources is the #1 killer — ONE book per subject, read it completely
- This attempt: build the base. Real competition starts attempt 2.
""",
    "2": """
Second attempt. You know the terrain — now go deeper, not wider.
- Same books, better retention. No new resources unless critical gap.
- Do a hard gap analysis: where exactly did Prelims marks bleed out?
- Answer writing must start month 1 — not month 4
- Revision cycles must be tighter — 3 full revisions minimum before Prelims
- Biggest trap: thinking new books = new results. They don't.
""",
    "3": """
Third attempt. This is serious territory — no room for the same mistakes.
- Mandatory: sit down and write exactly what went wrong in attempts 1 and 2
- If Prelims is the problem — mock test analysis is non-negotiable, daily
- If Mains is the problem — answer writing quality must jump dramatically
- Get answers evaluated externally — not just self-assessment
- Brutal truth: doing the same thing a third time will give the same result
""",
}

DEFAULT_ATTEMPT_GUIDANCE = """
4th attempt or beyond. Complete strategy reset required.
- What you've been doing clearly isn't working — identify it and stop
- Get a mentor or structured coaching — self-study alone has limits
- Diagnose precisely: Prelims failure or Mains failure? Attack that specifically
- This attempt must be fundamentally different from the previous ones
"""

# ─────────────────────────────────────────
# OPTIONAL SUBJECT CONTEXTS
# ─────────────────────────────────────────

OPTIONAL_CONTEXTS = {
    "Not decided": {
        "context": "Optional not chosen yet. Decision needed urgently.",
        "plan_instruction": """
Decide in the next 2 weeks. Not deciding is a decision to fail.
How to choose:
- Background: Your graduation subject or something you genuinely studied
- Overlap with GS: Geography (GS1), Public Admin (GS2), Sociology (GS1/2), History (GS1)
- Scoring trends: Anthropology, Geography, Pub Ad, Sociology score consistently high
- Interest: You'll spend 400+ hours on this. Pick something you won't dread.

Top 3 recommendations:
1. Geography — Scientific, map-based, heavy GS1 overlap. Abundant material.
2. Anthropology — Small syllabus, high scoring, diagrams boost marks.
3. Public Administration — Structured, GS2 overlap, good for analytical thinkers.
""",
    },
    "History": {
        "context": "History optional — large syllabus, strong GS1 overlap.",
        "plan_instruction": """
Paper 1 (Ancient & Medieval):
- Ancient: R.S. Sharma — "Ancient India"
- Medieval: Satish Chandra — "Medieval India: From Sultanate to the Mughals"

Paper 2 (Modern + World History):
- Modern India: Bipan Chandra — "India's Struggle for Independence"
- World History: Norman Lowe — "Mastering Modern World History"

Timeline: Paper 1 — 2.5 months. Paper 2 — 2 months. Revision — 1 month.
Target: 280-300/500
""",
    },
    "Geography": {
        "context": "Geography optional — excellent GS1 overlap, consistently high scoring.",
        "plan_instruction": """
Paper 1 (Physical Geography):
- Savindra Singh — "Physical Geography"
- G.C. Leong — "Certificate Physical and Human Geography"

Paper 2 (Human/Economic + India Geography):
- Majid Husain — "Geography of India" and "World Geography"

Maps: Draw one map daily. Non-negotiable.
Timeline: Paper 1 — 3 months. Paper 2 — 2 months.
Target: 300-320/500
""",
    },
    "Public Administration": {
        "context": "Public Administration — structured syllabus, strong GS2 overlap.",
        "plan_instruction": """
Paper 1: Mohit Bhattacharya — "New Horizons of Public Administration"
Paper 2: Ramesh K. Arora & Rajni Goyal — "Indian Public Administration"
Thinkers: Taylor, Fayol, Weber, Simon, Maslow — master all.
Timeline: Paper 1 — 2 months. Paper 2 — 2 months.
Target: 290-310/500
""",
    },
    "Sociology": {
        "context": "Sociology — conceptual, moderate syllabus, good scoring.",
        "plan_instruction": """
Paper 1: Haralambos & Holborn — "Sociology: Themes and Perspectives"
Paper 2: Yogendra Singh — "Modernization of Indian Tradition"
Thinkers: Marx, Durkheim, Weber, Parsons — master these first.
Timeline: Paper 1 — 2 months. Paper 2 — 1.5 months.
Target: 290-310/500
""",
    },
    "Anthropology": {
        "context": "Anthropology — small syllabus, consistently high scoring.",
        "plan_instruction": """
Paper 1 (Physical/Biological + Social-Cultural + Theories):
- P. Nath — "Physical Anthropology" (human evolution, genetics, variations)
- Ember & Ember — "Anthropology" (core concepts, Paper 1 & 2)
- Makhan Jha — "An Introduction to Anthropological Thought" (theories/thinkers)
Paper 2 (Indian Anthropology + Tribal issues):
- Nadeem Hasnain — "Indian Anthropology" and "Tribal India"
- IGNOU MA Anthropology notes for syllabus gaps
Diagrams: Practice daily — they are mark multipliers.
Timeline: Paper 1 — 2 months. Paper 2 — 1.5 months.
Target: 300-330/500
""",
    },
    "Political Science & International Relations": {
        "context": "PSIR — very popular optional, strong GS2/IR overlap, abundant material.",
        "plan_instruction": """
Paper 1 (Political Theory + Indian Govt & Politics):
- O.P. Gauba — "An Introduction to Political Theory"
- Sushila Ramaswamy — "Political Theory: Ideas and Concepts"
- M. Laxmikanth / D.D. Basu — Indian polity base
Paper 2 (Comparative Politics + International Relations):
- Andrew Heywood — "Global Politics"
- Pavneet Singh — "International Relations"
Thinkers: Plato to Gramsci — crisp notes with quotes.
Timeline: Paper 1 — 2 months. Paper 2 — 2 months.
Target: 280-300/500
""",
    },
    "Philosophy": {
        "context": "Philosophy — smallest syllabus, no current affairs, scoring if concepts are clear.",
        "plan_instruction": """
Paper 1 (Western + Indian Philosophy):
- Frank Thilly — "A History of Philosophy" (Western)
- C.D. Sharma — "A Critical Survey of Indian Philosophy"
Paper 2 (Socio-Political + Philosophy of Religion):
- O.P. Gauba — socio-political concepts
- Standard class notes for structured answers
Strength: tiny, fully static syllabus. Master concepts, write crisp.
Timeline: Paper 1 — 1.5 months. Paper 2 — 1.5 months.
Target: 270-300/500
""",
    },
    "Economics": {
        "context": "Economics — analytical optional, ideal for those with the background.",
        "plan_instruction": """
Paper 1 (Micro + Macro + Growth + International Econ):
- H.L. Ahuja — "Advanced Economic Theory" (micro)
- Dornbusch & Fischer — "Macroeconomics"
Paper 2 (Indian Economy):
- Uma Kapila — "Indian Economy: Performance and Policies"
- Economic Survey + Union Budget (latest)
Practice: diagrams, derivations, and data/graphs are mark boosters.
Timeline: Paper 1 — 2.5 months. Paper 2 — 2 months.
Target: 270-300/500
""",
    },
}

# ─────────────────────────────────────────
# WEAK AREA CONTEXTS
# ─────────────────────────────────────────

WEAK_AREA_CONTEXTS = {
    "Not specified": "No weak area specified. Give balanced advice across all GS subjects.",
    "Ancient Indian History": "NCERT Class 6-7 as base. Then R.S. Sharma's 'Ancient India'. Focus on dynasties, art & architecture, inscriptions.",
    "Medieval Indian History": "NCERT Class 7 first. Then Satish Chandra's 'Medieval India'. High-yield: Delhi Sultanate, Mughal system, Bhakti-Sufi movement.",
    "Modern Indian History": "Spectrum's 'Brief History of Modern India' — best for Prelims. Cover freedom struggle timeline, socio-religious reforms, important acts.",
    "Indian Polity & Constitution": "Laxmikanth's 'Indian Polity' — chapter by chapter, no skipping. Make an amendment tracker.",
    "Indian Economy": "NCERT Class 11-12 Economics for concepts. Ramesh Singh's 'Indian Economy'. Current Economic Survey summary.",
    "Environment & Ecology": "Shankar IAS 'Environment' — cover completely. Critical: biodiversity, climate change, protected areas, international conventions.",
    "Science & Technology": "Vision IAS Monthly S&T section + PIB science releases + The Hindu S&T page. Breadth over depth.",
    "Current Affairs": "The Hindu — max 45 minutes. Vision IAS Monthly Magazine. Filter: Polity, Economy, IR, Environment, S&T. Skip everything else.",
    "Answer Writing (Mains)": "3 answers daily. Structure every time: Intro → Body (2-3 dimensions) → Conclusion. Time yourself strictly.",
    "Ethics & Integrity": "Lexicon for Ethics + GS4 by Subba Rao. Write two 150-word answers daily — no other shortcut.",
    "Post-Independence India": "NCERT 'Politics in India Since Independence' (Class 12) + Bipan Chandra 'India Since Independence'. Focus: integration of states, wars, economic policy shifts, Emergency.",
    "World History": "Norman Lowe 'Mastering Modern World History' (Mains GS1). Focus: Industrial Revolution, World Wars, decolonization, Cold War.",
    "Indian Art & Culture": "Nitin Singhania 'Indian Art and Culture' — cover fully. High-yield for Prelims: architecture, dance, music, paintings, UNESCO sites.",
    "Indian & World Geography (Physical)": "NCERT (Class 11) + G.C. Leong 'Certificate Physical and Human Geography'. Focus: geomorphology, climatology, oceanography. Practice maps daily.",
    "Indian & World Geography (Human & Economic)": "NCERT (Class 12) + Majid Husain. Focus: resources, agriculture, industries, settlements, India's geography. Map practice non-negotiable.",
    "Indian Society & Social Issues": "NCERT Sociology (Class 11-12) + current affairs linkage. Focus: population, urbanization, communalism, role of women, globalization.",
    "Governance & Public Policy": "2nd ARC reports (summaries) + Laxmikanth 'Governance in India'. Focus: transparency, e-governance, citizen charters, RTI.",
    "Social Justice": "Current affairs + scheme notes. Focus: vulnerable sections, government schemes, health, education, poverty, hunger.",
    "International Relations": "Current affairs + Pavneet Singh 'International Relations'. Focus: India's bilateral/multilateral ties, groupings, global institutions.",
    "Agriculture & Food Security": "NCERT + current affairs. Focus: cropping patterns, MSP, subsidies, food processing, PDS, irrigation, agri-reforms.",
    "Disaster Management": "NDMA guidelines + Vision IAS notes. Focus: cyclones, floods, earthquakes, NDMA/SDMA framework, Sendai Framework.",
    "Internal Security": "Ashok Kumar & Vipul Anekant 'Internal Security and Disaster Management'. Focus: terrorism, naxalism, cyber security, border management, money laundering.",
    "Case Studies (Ethics Paper)": "Practice 4-5 case studies weekly. Structure: stakeholders -> ethical issues -> options -> best course with justification.",
    "CSAT (Paper 2)": "QUALIFYING but it fails many — do not ignore. Daily comprehension + Mrunal / Tata McGraw-Hill aptitude. Comfortably target 60+/200.",
    "Environment & Biodiversity (Prelims)": "Shankar IAS 'Environment'. Prelims-heavy: protected areas, species in news, conventions, climate reports.",
    "Economy (Prelims MCQs)": "NCERT + Ramesh Singh + current affairs economy. Practice PYQ MCQs — Prelims economy is fact and concept heavy.",
    "Essay Writing": "Write one full essay weekly. Focus: structure, multi-dimensional content, quotes, balanced view. Read toppers' essays.",
    "Time Management in Exam": "Full-length timed mocks weekly. Prelims: 2 rounds + OMR practice. Mains: strict per-question time, never leave blanks.",
    "Revision & Retention": "Active recall + spaced repetition. One-page summaries per topic. Revise 3+ times before Prelims. Flashcards help.",
    "Optional Subject": "Select your optional in the form for specific guidance. Generally: 2 standard books per paper + PYQs + topper notes + answer writing.",
}


def get_optional_context(optional: str) -> tuple[str, str]:
    """Get context and plan instruction for optional subject."""
    if optional in OPTIONAL_CONTEXTS:
        d = OPTIONAL_CONTEXTS[optional]
        return d["context"], d["plan_instruction"]

    if "Literature" in optional:
        lang = optional.replace(" Literature", "")
        return (
            f"{optional} — regional language optional. High scoring for native speakers.",
            f"Use standard {lang} university textbooks. Find toppers who took {lang} Literature.\nTimeline: 2 months per paper. Target: 290-310/500",
        )

    return (
        f"{optional} — research specific toppers' strategies before starting.",
        f"1. Download UPSC syllabus for {optional} from upsc.gov.in\n2. Find toppers' interviews\n3. Identify 2 standard books per paper\n4. Solve last 10 years PYQs",
    )


def get_weak_context(weak: str) -> str:
    """Get context for weak areas."""
    parts = [w.strip() for w in weak.split(",")]
    return "\n".join(
        [f"• {p}: {WEAK_AREA_CONTEXTS.get(p, WEAK_AREA_CONTEXTS['Not specified'])}" for p in parts]
    )


def get_weak_plan_instruction(weak: str, months_left: int) -> str:
    """Get plan instruction for weak areas based on time left."""
    parts = [w.strip() for w in weak.split(",")]
    results = []

    if months_left <= 1:
        plan_template = "1-Week Emergency Sprint:\n- Days 1-3: Read only high-yield sections\n- Days 4-5: Solve PYQs only\n- Days 6-7: Revise notes"
    elif months_left <= 3:
        plan_template = "3-Week Recovery:\n- Week 1: Read recommended source + make notes\n- Week 2: Solve 5 years PYQs + fill gaps\n- Week 3: Full revision + timed practice"
    else:
        plan_template = "4-Week Recovery:\n- Week 1: Read chapter by chapter + one-page notes\n- Week 2: Solve 5 years PYQs + mark gaps\n- Week 3: Topic-wise mock tests + fill gaps\n- Week 4: Full revision + answer writing"

    for p in parts:
        ctx = WEAK_AREA_CONTEXTS.get(p, WEAK_AREA_CONTEXTS["Not specified"])
        results.append(f"**{p}**\n{ctx}\n\n{plan_template}")

    return "\n\n".join(results)


def get_attempt_guidance(attempt_num: str) -> str:
    """Get guidance based on attempt number."""
    return ATTEMPT_GUIDANCE.get(attempt_num, DEFAULT_ATTEMPT_GUIDANCE)
