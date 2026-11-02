# Smoke checklist — the 8 workflows

Two layers:

1. **Offline (automated, every change):** `uv run pytest -q tests/test_workflow_smoke.py` — for one primary
   endpoint per workflow: route mounted, 401 without/with an invalid token, 422 for an invalid body with a valid
   session. No LLM, network or vector store. It does **not** prove the workflow produces a good answer.
2. **Live (manual, at phase close when keys are available):** the table below, against a running backend +
   frontend with real `GOOGLE_API_KEY` / `GROQ_API_KEY`, Qdrant and the per-workflow dependencies. Record the
   date and result in `docs/PROJECT_STATE.md` → workflow table. Anything not run stays UNVERIFIED.

All paths are under `/api/v1`, need `Authorization: Bearer <access_token>` (log in at `POST /auth/login`), and
are called by the frontend through `frontend/src/lib/api.ts`. Streaming endpoints return `text/plain` chunks;
job endpoints return `{"job_id", "status": "queued"}` and are polled at `GET /jobs/{job_id}` until `done`/`error`.

| # | Workflow | UI page | Endpoint(s) exercised | Sample request | Expected response | Needs |
|---|---|---|---|---|---|---|
| 1 | AI Mentor | `/app` → Mentor (`features/mentor/MentorChat.tsx`) | `POST /agent/chat/stream` | `{"question": "Explain the basic structure doctrine."}` | Streamed answer text; sources/citations block when web or KB used. Off-topic question → fixed scope-gate reply. | LLM key; Qdrant (mentor KB) optional; web search optional |
| 2 | NCERT Study | NCERT (`features/ncert/Ncert.tsx`) | `GET /ncert/classes` → `/ncert/subjects/{class}` → `/ncert/chapters/{class}/{subject}` → `POST /ncert/study` → `POST /ncert/chat` | study: `{"class_name", "subject", "chapter"}` from the lists | Lists `{"items": [...]}`; study `{"notes", "mindmap_html", "questions_html", "chapter_path"}`; chat streams text | LLM key; NCERT PDFs (R2 or local `data/ncert/`); Qdrant |
| 3 | PYQ + Personal PYQ Bank | PYQ (`features/pyq/Pyq.tsx`) | `POST /pyq/generate`; bank: `POST /pyq/bank/upload` (PDF) → `GET /pyq/bank/status` → `POST /pyq/bank/generate` → `POST /pyq/bank/clear` | `{"topic": "Federalism", "question_type": "mcq", "num_questions": 5}` | Streamed questions; bank status `{"exists": true}` after upload | LLM key; Qdrant for the bank |
| 4 | Answer Evaluator | Evaluator (`features/evaluator/Evaluator.tsx`) | `POST /evaluator/evaluate/sync`, `/evaluator/mains/sync`, `/evaluator/model-answer/sync` | `{"question": "Discuss federalism in India.", "answer": "<≥50 chars>"}` | `{"response": str, "structured": {...}}` | LLM key |
| 5 | Current Affairs | Current Affairs (`features/current_affairs/CurrentAffairs.tsx`) | `GET /current-affairs/{dates,months,topics}`; `POST /current-affairs/{daily,editorial,monthly}` | daily: `{"date": "01 November 2026"}` | Lists for dates/months/topics; streamed digest text | LLM key; web search (live) |
| 6 | Study Planner | Planner (`features/planner/Planner.tsx`) | `POST /planner/generate` (stream), `/planner/generate/sync` | `{"goal": "UPSC 2027", "hours": "6"}` | Streamed plan; sync `{"plan": str, "meta": {...}}`. Plans are not persisted. | LLM key |
| 7 | Lecture Learning | Lecture (`features/lecture/Lecture.tsx`) | `POST /lecture/process` (YouTube) or `/lecture/process-text` → poll `/jobs/{id}` → `POST /lecture/chat` | process-text: `{"transcript": "<pasted text>", "medium": "English"}`; chat: `{"question": "What is the Preamble?", "video_id": "<video_id from the job result>", "topic_info": <topic_info from the job result>, "chat_history": []}` | Job → result `{"notes", "topic_info", "video_id", "mindmap_html", "questions_html"}`; chat streams text | LLM key; YouTube reachability for `/process`; Qdrant |
| 8 | PDF / Document Chat | Upload (`features/upload/Upload.tsx`) | `POST /upload/process` (multipart `file`) → poll `/jobs/{id}` → `POST /upload/chat?pdf_hash=<hash>` | chat: `{"question": "Summarise chapter one."}` | Job result `{"success", "filename", "hash", "book_info", "notes"}`; chat streams text | LLM key; Qdrant; a text-based PDF (no OCR) |

## Pass criteria per row
- HTTP 200 (or 200 job + `done`), non-empty output, no error banner in the UI, no stack trace or provider text in
  the response, loading/error states shown by the page.
- Note latency and the LLM provider that answered (Gemini vs Groq fallback) if visible in logs.
