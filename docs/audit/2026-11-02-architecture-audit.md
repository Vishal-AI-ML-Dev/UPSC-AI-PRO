# Architecture audit — 2026-11-02 (baseline)

Read-only static audit of commit `9e64c4e` (branch `main`). No tests, app code or migrations were run.
Statuses: CONFIRMED (from source), PARTIAL, MISSING, UNVERIFIED (needs runtime). This file is the condensed,
persisted version of the full audit delivered in the session; section letters match that report.

## A. Executive summary
| # | Finding | Status |
|---|---|---|
| 1 | Jev / TypeSafe: zero references in source, tests, `uv.lock`, `package-lock.json` or `.venv` | MISSING |
| 2 | "Supervisor" = one structured-output LLM classifier → exactly one of 5 subgraphs; no parallelism; only via `POST /api/v1/agent/chat/stream` | CONFIRMED |
| 3 | Supervisor `rag` branch unreachable over HTTP (`route_selector` sends rag→mentor without `persist_key`; no route sets it) | CONFIRMED |
| 4 | `src/agents/*/graph.py` are plain functions, not LangGraph; NCERT/PYQ/Lecture/Upload bypass the supervisor | CONFIRMED |
| 5 | One real tool-using agent: `src/graph/tools.py:build_tool_agent` (2 tools, 3-round cap) | CONFIRMED |
| 6 | Chroma fallback imports `langchain_chroma`, which is not declared/locked/installed | CONFIRMED (static) |
| 7 | Auth-endpoint rate limit (`auth_rate_limit_*`) configured and documented, never used | CONFIRMED |
| 8 | Upload vector collections keyed by MD5, no owner; mentor tool accepts model-chosen `persist_key` | CONFIRMED |
| 9 | Jobs: in-process `ThreadPoolExecutor(2)`, status-only persistence, no retries; rq backend / `src/worker.py` absent | CONFIRMED |
| 10 | Weak-topic tracking, revision, experiments, plan persistence are placeholders/missing | CONFIRMED |
| 11 | `compose_grounded_answer` never called; `tenacity` never imported; "stateless" claim false | CONFIRMED |

## C. Stack (locked versions)
fastapi 0.137.0 · starlette 1.3.1 · uvicorn 0.49.0 · langgraph 1.2.5 · langgraph-checkpoint-postgres/sqlite 3.1.0 ·
langchain-core 1.4.7 · langchain-google-genai 4.2.5 · langchain-groq 1.1.3 · qdrant-client 1.18.0 ·
langchain-qdrant 1.1.0 · sqlalchemy 2.0.50 · alembic 1.18.5 · psycopg 3.3.4 · python-jose 3.5.0 · bcrypt 5.0.0 ·
pypdf 6.13.2 · yt-dlp 2026.6.9 · boto3 1.43.122 · ddgs 9.16.1 · langchain-tavily 0.2.18 · langfuse 4.7.1 ·
sentry-sdk 2.64.0 · mcp 1.28.1 · pytest 9.1.0 · ruff 0.15.17.
Frontend: react 18.3.1 · react-router-dom 6.30.4 · @tanstack/react-query 5.101.2 · react-markdown 9.1.0 ·
rehype-sanitize 6.0.0 · recharts 2.15.4 · vite 5.4.21 · typescript 5.9.3 · tailwindcss 3.4.19.
No direct import found: passlib, tenacity, youtube-transcript-api, tavily-python, langchain meta-package.
Imported/configured but not installed: langchain-chroma, fastembed, langchain-ollama, langchain-openai,
sentence-transformers, cohere, rq/redis.

## D. Backend
- Middleware (outer→inner): RequestId → HttpMetrics → MaxUploadSize → RateLimit → Timeout(90 s) → SecurityHeaders → CORS.
- `get_current_user` validates the HS256 JWT and checks `sid == users.active_session_id` per request (fail closed).
- `src/api/routes/mentor.py` is not registered in `src/api/main.py`.
- Blocking work inside `async def`: upload/lecture `?sync=true`, `routes/history.py`, `auth.forgot_password`,
  `auth.resend_verification`.
- `?sync=true` branches return `f"Processing failed: {e}"`.

## F/G. LangGraph
| Graph | File | Status |
|---|---|---|
| Supervisor (router) | `src/graph/supervisor.py` | ACTIVE via `/agent/chat/stream`; checkpointer Postgres→SQLite→memory |
| Mentor ReAct agent | `src/graph/tools.py` | ACTIVE; full thread history sent each turn (unbounded) |
| CRAG RAG | `src/graph/rag_graph.py` | UNREACHABLE over HTTP; used in `src/eval/llm_eval.py` |
| Planner/Evaluator/CA wrappers | `src/graph/agent_subgraphs.py` | ACTIVE; inputs from regex (task_inputs never set) |
| Plan-execute, reflection, HITL | `src/graph/plan_execute.py`, `reflection.py` | OFF by default |

## I. LLM
`get_llm()`: gemini-2.5-flash → flash-lite → groq llama-3.3-70b · `get_fast_llm()`: flash-lite → llama-3.1-8b → flash.
Temp 0.3/0.2, 4096 max output (Gemini), 45 s per-call timeout, timeout does not cascade to fallbacks. No cost caps.

## J. RAG
pypdf (no OCR) → `RecursiveCharacterTextSplitter` 1000/150 → Gemini embeddings pinned to 768 → Qdrant collection
per `persist_key` → dense top-5 → lexical re-score of the same candidates + RRF → threshold 0.3 → LLM →
form-level citations. Cross-encoder, multi-query, HyDE: off. Response cache: Upstash, exact + semantic (0.92),
24 h, user/thread scoped.

## K. Database
Tables: users, conversations, messages, feedback (user_id no FK), password_reset_tokens,
email_verification_tokens, refresh_tokens, jobs (user_id no FK, nullable). Migrations 0001–0004.
LangGraph checkpoint/store tables created at runtime outside Alembic. No tables for plans, mastery, revision,
upload ownership.
Vector collections: `mentor_kb` (global), `ncert_v2_*` (global, allow-listed), `lecture_<video_id>` (global),
`upload_<md5>` (global, no owner), `pyqbank_<user_id>` (per user by name only).

## M. Security findings
| Sev | Finding | Location |
|---|---|---|
| HIGH | No object-level authorization at vector boundary; model-chosen `persist_key` | `src/graph/tools.py:knowledge_base_tool_fn`, `src/api/routes/upload.py:chat_upload` |
| MEDIUM | Global upload dedup + shared `_processed_cache` | `src/agents/upload/graph.py` |
| MEDIUM | Auth rate limit unused; "Email already registered" enumeration | `src/api/rate_limit.py`, `src/core/users.py` |
| MEDIUM | Tokens in localStorage | `frontend/src/lib/api.ts` |
| MEDIUM | No `max_length` on chat/transcript/history inputs | `src/schemas.py`, `src/api/routes/chat.py` |
| MEDIUM | Upload limit via Content-Length only; whole file in memory | `src/api/upload_limit.py` |
| LOW | Exception text in responses / job errors | upload, lecture, pyq routes; `src/core/job_queue.py` |
| LOW | 6-char password minimum | `src/core/users.py` |
| LOW | Emails and query text in logs | `users.py`, `auth.py`, `observability.log_retrieval_metrics` |
| LOW | Public `/docs` in production; compose default creds and exposed ports | `src/api/main.py`, `docker-compose.yml` |
| INFO | Unmounted unauthenticated duplicate upload router; MCP SSE server without auth | `src/agents/upload/upload.py`, `src/mcp_server.py` |
Strengths: bcrypt, rotating hashed refresh tokens, DB-checked single session, explicit CORS, security headers,
NCERT allow-listing, `harden_untrusted` fencing, escaped study-aid HTML, sanitized Markdown.

## N. Background jobs
In-process, inputs not persisted, no retry/timeout/cancel, `reap_stale_jobs` on every process boot fails jobs of
sibling workers, executor never shut down.

## O. Tests
66 modules, 574 `def test_` functions (+66 parametrize decorators), offline, LLM mocked. Strong auth/session
coverage. No frontend tests, no load tests, no Postgres/Qdrant integration. Live smoke CI job disabled; ruff and
prettier non-blocking. Nightly LLM eval targets the unreachable CRAG graph.

## P. Deployment
Docker (non-root, HEALTHCHECK `/health`), compose (postgres, qdrant:latest, unused redis, backend, nginx frontend),
Render free plan, Vercel frontend. `/health` is static. No readiness, Prometheus, backup or restore procedures.

## T. Documentation discrepancies
See `docs/PROJECT_STATE.md` → Doc drift.

## U. Risk register
See `docs/PROJECT_STATE.md` → Open risks (same ranking).
