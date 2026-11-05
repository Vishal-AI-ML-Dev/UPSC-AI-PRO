# UPSC AI Pro — project brief (2–3 minute read)

**Title:** UPSC AI Pro — AI-Powered UPSC Learning Platform
**Owner / author:** Vishal Shivhare — GenAI / Backend Engineer
**One line:** A web platform with eight study workflows, an AI Mentor that routes questions to the right specialist (LangGraph), answers grounded in
source documents (retrieval-augmented generation), per-user data security, automated tests and cloud deployment.

**Links:** [Live app](https://upsc-ai-pro.vercel.app/) · [API docs](https://upsc-ai-pro-puih.onrender.com/docs) · [GitHub](https://github.com/Vishal-AI-ML-Dev/UPSC-AI-PRO)
(The free hosting plan sleeps when idle, so the first load can take a while. Link status was supplied by the owner and was not re-checked when this page was written.)

## The problem and the solution
Preparing for India's UPSC Civil Services exam means covering a huge syllabus and practising answer writing with little feedback. Ordinary chatbots can
sound confident and be wrong, and cannot say where an answer came from.
UPSC AI Pro puts the main preparation activities in one app and, where possible, **answers from the student's own documents or a curated knowledge base**
and shows its sources. Plain-language note: "retrieval-augmented generation (RAG)" means the AI first looks up relevant passages, then writes its answer from them.

## My role
I owned the product and the engineering direction, and I used an AI coding assistant (Claude Code) for much of the
implementation. I did not hand-write every line of code, and I describe the split plainly:

| Area | What I did |
|---|---|
| **Product direction and requirements** | Chose the problem, defined the eight workflows, set priorities and the scope of each phase, and decided what was out of scope. |
| **Architecture and technical supervision** | Directed the design (FastAPI backend, LangGraph routing for the mentor chat, retrieval with Qdrant, per-user data isolation, background jobs). Approved a written specification and plan before each phase, reviewed the results and recorded decisions as architecture decision records. |
| **AI-assisted implementation** | Claude Code wrote most of the code and tests within the approved specs and plans, under project rules in the repository (`CLAUDE.md`), with review passes (code quality, security) before a phase was closed. |
| **Integration, debugging, testing and verification** | Reviewed diffs and test evidence, and ran manual browser acceptance checks on the features (recorded in the phase reports). Findings from audits and reviews were fed back and fixed. |
| **Production ownership and operations** | Responsible for the hosting accounts, secrets and production releases; the database-migration release was confirmed by me (as recorded in the project's release notes). I own the open operational items, such as backups and error-monitoring activation, which are documented as not yet done. |

The repository's specs, plans, decision records and phase reports (`.claude/specs`, `.claude/plans`, `docs/DECISIONS.md`, `docs/phase-reports/`)
document this process. This page makes no statement about team size, customers, revenue or user numbers.

## Eight product capabilities
1. **AI Mentor** — chat that routes each message to a mentor, planner, answer-evaluator or current-affairs specialist and can search the web and a knowledge base.
2. **NCERT Study** — notes, practice questions and Q&A for a chosen textbook chapter.
3. **PYQ + Personal PYQ Bank** — previous-year-style questions, plus a private bank built from your own paper.
4. **Answer Evaluator** — scores and critiques written answers; results feed a progress dashboard.
5. **Current Affairs** — daily, editorial and monthly digests from public news feeds.
6. **Study Planner** — personalised, saved study plan with progress tracking.
7. **Lecture Learning** — notes and chat from a YouTube lecture or pasted transcript.
8. **PDF / Document Chat** — ask questions of an uploaded PDF.

## Main technologies
Python 3.13 · FastAPI · LangGraph · Google Gemini (with Groq as backup) · retrieval-augmented generation with Qdrant vector search · PostgreSQL (Supabase) ·
JWT authentication · React + TypeScript · Docker · GitHub Actions · Vercel (frontend) · Render (backend) · Cloudflare R2 (file storage).
Optional/not active: Sentry error monitoring and Langfuse tracing exist in code but are not active or not confirmed active in production.

## Strongest engineering achievements (each backed by code and tests in the repository)
1. **Secure multi-user design.** Every user's data, documents and chat state are isolated; the AI model is not allowed to choose which document collection it reads.
   An earlier audit found a high-severity gap here; it was closed and covered by ownership tests.
2. **Prompt-injection defence with measured findings.** Uploaded documents, web text and user answers are fenced as untrusted before reaching the model. An offline evaluation
   found 21 prompt-construction defects; all were fixed and are now regression tests. *(This tests how prompts are built with scripted models — it is not a live-model safety test.)*
3. **Honest AI architecture.** A LangGraph supervisor routes mentor chat to one of four specialists; a tool-using agent is capped at three tool rounds. A routing bug that sent
   most later messages to the wrong specialist was found and fixed (0 of 200 misrouted in an offline test that uses scripted stand-in models).
4. **Reliability engineering.** Provider fallback (Gemini → Groq), timeouts, circuit breaker, rate limiting, and a background-job system with recovery of stuck jobs.
5. **Disciplined delivery.** 1,847 collected automated offline test cases (mocked AI services), CI on every push, versioned database migrations, and written specs, architecture decisions and phase reports.

## Verified numbers (with their limits)
| Fact | Value | Important qualification |
|---|---|---|
| Automated tests | 1,847 collected offline test cases | Counted on 2026-11-05 on a working copy with uncommitted changes; they use mocked AI services, not production |
| REST API endpoints | 54 | Counted from the generated API schema |
| Product workflows | 8 | Workflows, not eight independent AI agents |
| Code size | about 20,000 lines of Python (backend) and 7,700 lines of TypeScript (frontend) | Physical lines, not a count of logical statements |
| Routing test | 160/200 → 0/200 misrouted | Offline test with scripted stand-in models |
| Throughput test | roughly 13× in one synthetic local scenario | Local simulation, not production traffic |
| Retrieval test | 97 questions; ~99 % of expected passages found | Small, mostly synthetic data with AI-written labels; **not** proof of production quality |

**Not claimed:** user numbers, revenue, production speed or cost, a hallucination-reduction percentage, or active error monitoring. These have not been measured.

More detail: [PROJECT_HIGHLIGHTS.md](PROJECT_HIGHLIGHTS.md) · [ENGINEERING_METRICS.md](ENGINEERING_METRICS.md) · [README](../README.md)
