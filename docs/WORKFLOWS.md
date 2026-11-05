# The eight study workflows

UPSC AI Pro exposes eight user-facing workflows. They share authentication, the LLM layer and (for six of them) the vector
store, but they do **not** share one orchestration framework: only the AI Mentor goes through the LangGraph supervisor
([AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md)). Routes below were read from `src/api/routes/*.py` and cross-checked against
`app.openapi()` on 2026-11-05. All routes sit under `/api/v1` and require a JWT unless stated.

Offline evidence for every workflow: the offline pytest suite (1,847 collected cases) and the Phase 07 T1/T2 evals call the
real production functions with scripted fake models. **No workflow has recorded real-model quality or production latency
measurements (UNVERIFIED).**

| # | Workflow | Frontend | Backend route module | Orchestration |
|---|---|---|---|---|
| 1 | AI Mentor | `features/mentor/MentorChat.tsx` | `routes/chat.py` | LangGraph supervisor + ReAct agent |
| 2 | NCERT Study | `features/ncert/Ncert.tsx` | `routes/ncert.py` | Plain functions |
| 3 | PYQ + Personal PYQ Bank | `features/pyq/Pyq.tsx` | `routes/pyq.py` | Plain functions + background job |
| 4 | Answer Evaluator | `features/evaluator/Evaluator.tsx` | `routes/evaluator.py` | Plain functions |
| 5 | Current Affairs | `features/current_affairs/CurrentAffairs.tsx` | `routes/current_affairs.py` | Plain functions |
| 6 | Study Planner | `features/planner/Planner.tsx` | `routes/planner.py` | Plain functions + persisted plan |
| 7 | Lecture Learning | `features/lecture/Lecture.tsx` | `routes/lecture.py` | Plain functions + background job |
| 8 | PDF / Document Chat | `features/upload/Upload.tsx` | `routes/upload.py` | Plain functions + background job |

---

## 1. AI Mentor
- **Purpose:** conversational UPSC doubts, advice, and — through routing — plan, answer-evaluation and current-affairs requests typed into chat.
- **Route:** `POST /agent/chat/stream` (body: `question` ≤ 4,000 chars, optional `thread_id`). History is saved/listed through `/history/*` (`GET /history/conversations`, `GET /history/conversations/{id}/messages`, `POST /history/messages`).
- **Path:** scope gate → response cache → supervisor (one structured routing call per message) → mentor / planner / evaluator / current-affairs subgraph. The mentor is a ReAct agent with `web_search` and `knowledge_base_search` (max 3 tool rounds).
- **Dependencies:** Gemini (router on the fast model, mentor on the main model), Groq fallback, Mentor KB in Qdrant, optional web search, optional Upstash cache.
- **Storage:** LangGraph checkpoint and store (PostgreSQL in production); `conversations` and `messages` tables for session history; student profile in the store.
- **Output:** `text/plain` stream; the answer arrives whole rather than token by token.
- **Citations:** "Sources used" block built from tool results (web and KB). No claim-level verification.
- **Limitations:** extra router call per message; cache can serve stale answers; real-model quality UNVERIFIED.

## 2. NCERT Study
- **Purpose:** study a chosen class/subject/chapter: generated notes, mind-map, practice questions, and Q&A grounded in the chapter.
- **Routes:** `GET /ncert/classes`, `GET /ncert/subjects/{class_name}`, `GET /ncert/chapters/{class_name}/{subject}`, `POST /ncert/study`, `POST /ncert/chat`.
- **Path:** `agents/ncert/graph.py`: reads an allow-listed chapter PDF page by page, builds page-aware chunks, indexes them into a chapter collection, then `ask_ncert` retrieves with `similarity_search_with_sources` (k = 5).
- **Dependencies:** Gemini, Qdrant, **Cloudflare R2** (or local PDFs). The NCERT library is empty without one of them.
- **Storage:** NCERT PDFs in R2 (not in Git or the image); chapter vectors in Qdrant.
- **Output:** `/study` returns JSON; `/chat` streams `text/plain`. Study aids are HTML-escaped server-side.
- **Citations:** `[S#]` tags plus a footer of cited excerpts. **Format is validated; support is not.**
- **Limitations:** needs the PDF source; retrieval quality on NCERT is UNVERIFIED (empty in the benchmark); production behaviour is owner-reported only.

## 3. PYQ + Personal PYQ Bank
- **Purpose:** generate previous-year-style questions by topic/type, parse pasted questions, and build a personal question bank from the user's own PDF.
- **Routes:** `POST /pyq/generate` (stream), `POST /pyq/parse`, `GET /pyq/topics/{question_type}`, `POST /pyq/bank/upload` (background job), `POST /pyq/bank/generate` (stream), `GET /pyq/bank/status`, `POST /pyq/bank/clear`.
- **Path:** `agents/pyq/graph.py`; bank questions are retrieved with `similarity_search` (k = 8) from the user's own `pyqbank_<user id>` collection.
- **Dependencies:** Gemini, Qdrant (bank), job queue (bank upload).
- **Storage:** per-user Qdrant collection; job status in `jobs`.
- **Output:** streamed text; parse and status return JSON; bank upload returns a `job_id` to poll.
- **Citations:** none structured.
- **Limitations:** bank append is not idempotent, so the upload job does not auto-retry; a job is not resumable if the process dies.

## 4. Answer Evaluator
- **Purpose:** score and critique a written answer (basic and Mains formats) and produce a model answer.
- **Routes:** `POST /evaluator/evaluate/sync`, `POST /evaluator/mains/sync`, `POST /evaluator/model-answer/sync`.
- **Path:** `agents/evaluator/graph.py`; model output is parsed (`src/core/eval_parse.py`) and the score is bounded to `[0, max]`. The student's answer and question are fenced as untrusted input.
- **Dependencies:** Gemini/Groq only; no retrieval.
- **Storage:** `evaluation_attempts` (migration 0006) records attempts best-effort with an optional allow-listed subject; the Dashboard derives average score, subject mastery, weak subjects and revision hints from it.
- **Output:** JSON with the parsed evaluation.
- **Citations:** none (no retrieval).
- **Limitations:** scoring quality has not been compared with human examiners; a model can be wrong or lenient (UNVERIFIED).

## 5. Current Affairs
- **Purpose:** daily digest, editorial analysis and monthly digest.
- **Routes:** `POST /current-affairs/daily`, `/editorial`, `/monthly` (streams); `GET /current-affairs/topics`, `/dates`, `/months`.
- **Path:** `agents/current_affairs/graph.py` reads live news feeds (PIB, The Hindu, Indian Express, Down To Earth) and prompts the LLM with the fenced feed text; a failed feed returns an explicit notice instead of an invented digest.
- **Dependencies:** public feeds/web, Gemini/Groq. No Qdrant retrieval on the page route.
- **Storage:** none beyond optional ingest helpers (`ingest.py`, `monthly_ingest.py`).
- **Output:** streamed text.
- **Citations:** a fixed note naming the feed sources; no per-claim links.
- **Limitations:** feeds expose only recent headlines (older dates/months may be thin); live-web availability UNVERIFIED in production.

## 6. Study Planner
- **Purpose:** generate a personalised study plan and track progress against it.
- **Routes:** `POST /planner/generate` (stream), `POST /planner/generate/sync`, `GET|PUT|DELETE /planner/plan`, `PATCH /planner/plan/progress`.
- **Path:** `agents/planner/graph.py`; blank "weak areas" are filled from the caller's evaluation weak subjects first, then the Mentor profile, each fail-open.
- **Dependencies:** Gemini/Groq.
- **Storage:** `study_plans` (migration 0005): the latest plan per user, saved atomically; unsaved drafts are preserved in the UI.
- **Output:** streamed Markdown or structured JSON; plan CRUD returns JSON envelopes.
- **Citations:** none.
- **Limitations:** one saved plan per user; plan quality is not evaluated against real aspirants.

## 7. Lecture Learning
- **Purpose:** turn a YouTube lecture or a pasted transcript into notes and a chat grounded in that lecture.
- **Routes:** `POST /lecture/process` (URL, background job), `POST /lecture/process-text` (pasted transcript, background job), `POST /lecture/chat` (stream).
- **Path:** `agents/lecture/graph.py`: transcript via yt-dlp (or pasted text, ≤ 200,000 characters) → notes → chunks into a lecture collection → `ask_lecture` retrieves with `similarity_search` (k = 5). Transcripts and metadata are fenced as untrusted.
- **Dependencies:** yt-dlp/YouTube (reachability from Render is UNVERIFIED), Gemini, Qdrant, job queue.
- **Storage:** Qdrant collection (pasted lectures are user-scoped; YouTube ids are global); job status in `jobs`.
- **Output:** job result JSON, then streamed chat text.
- **Citations:** none structured.
- **Limitations:** jobs are not resumable; one transient retry; YouTube access can fail.

## 8. PDF / Document Chat
- **Purpose:** upload a PDF, then ask questions answered from it.
- **Routes:** `POST /upload/process` (background job), `POST /upload/chat` (stream).
- **Path:** `agents/upload/graph.py`: size/type validation → pypdf text → chunks → collection `upload_<user id>_<md5>` → `ask_upload` retrieves with `similarity_search` (k = 5). Document text is fenced as untrusted.
- **Dependencies:** Gemini, Qdrant, job queue.
- **Storage:** per-user Qdrant collection; bounded per-user in-process cache; job status in `jobs`.
- **Output:** job result JSON, then streamed chat text.
- **Citations:** none structured.
- **Limitations:** no OCR (scanned PDFs fail); up to 50 MB per file (`MAX_UPLOAD_MB`); a partially indexed upload is not repaired; no per-user active-job cap.

---

## Supporting routes (not workflows)
`/auth/*` (register, login, refresh, logout, verify/reset email, `me`), `/feedback/submit`, `/progress/overview`, `/jobs/{job_id}`, and the admin dashboards `/cost/overview`, `/monitoring/overview`, `/experiments/overview` (admin-only via `require_admin`; the sibling `*/access` probes tell the UI whether the caller is an admin). See [API_OVERVIEW.md](API_OVERVIEW.md).
