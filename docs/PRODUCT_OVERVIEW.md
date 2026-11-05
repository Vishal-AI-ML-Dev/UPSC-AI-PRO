# Product overview

## The problem
The UPSC Civil Services Examination takes most aspirants a year or more to prepare for. The syllabus is very large and spread over
NCERT textbooks, standard reference books, daily current affairs, previous-year questions (PYQs) and a lot of practice in answer writing.
Aspirants usually stitch together PDFs, coaching notes, video lectures and a separate timetable, and rarely get fast, objective
feedback on what they write.

General-purpose chatbots help with explanations but are risky for a precision exam: they can state article numbers, dates or committee
names with confidence and be wrong, they cannot show where an answer came from, and they have no knowledge of a student's own notes.

## What UPSC AI Pro is
A web application that gathers the main preparation activities in one place and grounds the AI's answers in sources where it can.
It is a **learning-support tool**, not a replacement for textbooks, teachers or official notifications.

## Target users
- UPSC aspirants (self-study or alongside coaching) who want help with doubts, notes, practice questions and planning.
- Students who want to ask questions of **their own** PDFs, lecture transcripts and question banks.
- There are no recorded user numbers (UNVERIFIED / not measured).

## Why it exists
It is a portfolio-grade, end-to-end GenAI engineering project: it demonstrates retrieval-augmented generation, tool-using agents, secure
multi-user design, background processing, automated testing and cloud deployment on a real domain problem. The honest limits of each
part are documented, not hidden ([Known limitations](#product-limitations)).

## Main capabilities
- **Ask and learn:** a conversational AI Mentor that routes each message to the right specialist and can search the web and a curated knowledge base.
- **Study from sources:** NCERT chapter notes and Q&A, chat with the student's own PDFs and lecture transcripts.
- **Practise:** PYQ-style question generation and a personal question bank built from the student's own paper PDF.
- **Get feedback:** an answer evaluator for basic and Mains-style answers, with a progress dashboard (average score, subject mastery, weak subjects).
- **Stay current and organised:** daily/monthly current-affairs digests from public feeds and a saved study planner.

## The eight workflows
| Workflow | One-line purpose |
|---|---|
| AI Mentor | Doubts, advice and routed help (plan, evaluate, current affairs) with web and knowledge-base tools |
| NCERT Study | Notes, mind-map, practice questions and grounded Q&A for a chosen chapter |
| PYQ + Personal PYQ Bank | Generate PYQ-style questions; build a private bank from your own PDF |
| Answer Evaluator | Score and critique an answer; model answer; attempts feed the dashboard |
| Current Affairs | Daily digest, editorial analysis, monthly digest from public feeds |
| Study Planner | Personalised plan, saved per user, with progress tracking |
| Lecture Learning | Notes and chat from a YouTube lecture or pasted transcript |
| PDF / Document Chat | Ask questions of an uploaded PDF |

Per-workflow routes, storage and citation behaviour: [WORKFLOWS.md](WORKFLOWS.md). These are **eight workflows, not eight autonomous
agents**: only the mentor chat uses LangGraph routing ([AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md)).

## High-level architecture
A React single-page app on Vercel talks over HTTPS with JWT authentication to a FastAPI backend on Render. The backend uses Gemini (with a Groq
fallback) for language models and embeddings, Qdrant for vector search, PostgreSQL (Supabase) for application data and conversation state, and
Cloudflare R2 for NCERT PDFs. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Product limitations
- **Answer quality has not been measured on real students or with real-model evaluation.** Treat outputs as study aids and verify facts.
- Citations exist only where the workflow produces them (mentor chat and NCERT), and they show *what was retrieved*, not proof that every sentence is supported.
- The retrieval relevance threshold does not reliably reject off-topic questions on the benchmark data, so a weak answer can still be shown with unrelated context.
- Scanned PDFs are not supported (no OCR); YouTube lecture access can fail; NCERT content depends on an external storage bucket.
- Mentor replies appear once complete rather than word by word.
- Hosted on free tiers: the first request after idle time is slow, and background jobs do not resume if the server restarts.
- No production usage statistics, uptime figures or latency measurements are recorded.
- Not affiliated with, or endorsed by, UPSC or NCERT.
