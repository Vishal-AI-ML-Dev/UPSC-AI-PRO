<div align="center">

# UPSC AI Pro

**A production-grade, retrieval-grounded AI system for UPSC preparation.**

Designed and built end to end — FastAPI + LangGraph backend, React/TypeScript frontend, RAG with dense retrieval and lexical re-ranking, and a reliability layer built for real free-tier constraints.

> **Deployment status:** being redeployed on new infrastructure. Live demo and API docs links will be added here after deployment (`<live-demo-url>`, `<api-docs-url>`). See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

![CI](https://img.shields.io/badge/CI-lint_%C2%B7_tests_%C2%B7_build_%C2%B7_LLM--eval-2088FF?style=flat-square)
![Tests](https://img.shields.io/badge/tests-930_offline-22C55E?style=flat-square)
![License](https://img.shields.io/badge/license-MIT_(recommended)-blue?style=flat-square)

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1C3C3C?style=flat-square)
![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)
![Postgres](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-DC244C?style=flat-square&logo=qdrant&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)

</div>

> **Reviewer TL;DR** — 8 product workflows: every feature page's route calls its feature functions directly (NCERT, PYQ, evaluator, current affairs, planner, lecture, PDF chat); the LLM-routed LangGraph supervisor (one structured routing call → mentor / planner / evaluator / current-affairs / rag) serves mentor chat only, with a ReAct tool-calling mentor; dense retrieval re-ranked by RRF over dense + lexical rankings, with optional (off-by-default) cross-encoder reranking and citations built from tool results; DB-persisted background-job status; circuit breakers, distributed rate limiting, request tracing; provider fallback (Gemini → Groq); 930 offline tests + an LLM-as-judge faithfulness gate in CI. Optional dependencies (cache, Redis, tracing, error monitoring) **fail open**, so the app degrades instead of crashing; Qdrant and the LLM providers are required.

---

## Table of Contents
1. [Problem Statement](#1-problem-statement)
2. [System Design](#2-system-design)
3. [Engineering Decisions](#3-engineering-decisions)
4. [Production Features](#4-production-features)
5. [Performance & Reliability Metrics](#5-performance--reliability-metrics)
6. [Failures & Lessons Learned](#failures--lessons-learned)
7. [Screenshots](#screenshots)
8. [For Reviewers & Recruiters](#6-for-reviewers--recruiters)

---

## 1. Problem Statement

**User problem.** UPSC preparation runs for years over a huge, fragmented syllabus (NCERTs, standard texts, daily current affairs, previous-year questions) and demands constant answer-writing practice with objective feedback. Aspirants stitch together PDFs, coaching notes, lectures, a separate planner, and have no trustworthy way to get *grounded*, syllabus-aware answers.

**Why existing solutions fail.**

| Approach | Why it falls short |
|---|---|
| Generic chatbots | Confidently invent article numbers, committees, dates — fatal for a precision exam |
| Static note apps / PDFs | No synthesis, no feedback loop, no retrieval |
| Single-prompt LLM wrappers | No grounding, no citations, no task specialisation, brittle under load |

**Why an LLM alone is insufficient.**
- **Stale + hallucinated facts** — training data is dated; exam dates, notifications, and cut-offs must come from live sources, not model memory.
- **No verifiability** — a serious aspirant needs to see *where* an answer came from.
- **One prompt can’t do everything** — grading a mains answer, building a timetable, and answering an NCERT question need different prompts, tools, and output contracts.

**Why agents + retrieval are required.**
- **Retrieval (RAG)** grounds answers in real source text and attaches citations, so claims are checkable and the model is constrained to the corpus.
- **Task-specific pipelines** give each feature its own prompts and output schema; for mentor chat a **supervisor** routes each request to the right handler, and the mentor agent calls **tools** (web search, knowledge base) only when needed — keeping trivial turns cheap and factual turns grounded.

---

## 2. System Design

### High-Level Architecture

```mermaid
flowchart TD
    U([User]) --> FE["Frontend — React + TypeScript (Vercel SPA)"]
    FE -->|HTTPS + JWT| GW["API Gateway — FastAPI"]
    GW --> SUP{{"Supervisor (mentor chat) — LangGraph"}}
    GW --> AG["Feature modules (NCERT, PYQ, lecture, PDF chat, …)"]
    SUP --> AG
    AG --> RET["Retrieval Layer — dense + RRF re-rank"]
    RET --> VDB[("Vector Database — Qdrant")]
    AG --> LLM["LLM Providers — Gemini → Groq"]
    GW -.-> OBS["Observability — Langfuse · Sentry · logs · metrics"]
    AG --> PG[("PostgreSQL — state · history · checkpoints")]
    style SUP fill:#6D28D9,color:#fff
    style LLM fill:#8E75B2,color:#fff
```

### Request Flow

```mermaid
flowchart TD
    A[User Query] --> B[Authentication — JWT]
    B --> C[Rate Limiting — Redis / in-proc]
    C --> D[Request Tracing — X-Request-ID]
    D --> E[Agent Routing — supervisor + model router]
    E --> F[RAG Pipeline — retrieve · re-rank]
    F --> G[Grounding — source citations + relevance gate]
    G --> H[Streaming Response — token by token]
    H --> I[Conversation Persistence — Postgres]
```

Middleware order is deliberate (outermost → innermost): `RequestId → HttpMetrics → MaxUploadSize → RateLimit → Timeout(90s) → SecurityHeaders → CORS`. Request-ID is outermost so **every** inner log line and the response carry the same correlation ID.

### RAG Architecture

| Stage | Implementation | Tradeoff |
|---|---|---|
| **Ingestion** | PDFs parsed with `pypdf`; current-affairs/NCERT/mentor KB ingested via `scripts/ingest_*.py` and agent ingest modules | Simple + dependency-light; no OCR (scanned PDFs unsupported) |
| **Chunking** | `RecursiveCharacterTextSplitter` (LangChain) — recursive separators preserve semantic boundaries | Fixed-size windows can still split mid-argument; no semantic chunker (added latency not justified yet) |
| **Embeddings** | Google `gemini-embedding-001`, **pinned to 768 dims** via a Matryoshka-truncate + L2-normalize wrapper | Managed API = no local GPU/OOM on 512 MB tiers; costs an API call per embed |
| **Vector indexing** | Qdrant collection per `persist_key` (`QDRANT_URL` server or `QDRANT_PATH` local). A Chroma fallback is coded but does not work: `langchain_chroma` is not installed | Managed dependency; Qdrant is required in every environment |
| **Retrieval** | **Dense** vector similarity; the candidates are then scored by a **lexical** signal (concept coverage with curated UPSC abbreviation expansion). There is no separate lexical index — the lexical signal only re-orders dense hits | Improves ordering for exact terms; cannot recover documents the dense search missed |
| **Fusion** | **Reciprocal Rank Fusion** (`1/(k+rank)`) of the dense and lexical rankings of the same candidates, instead of a linear score blend | Scale-independent + robust; loses absolute-score magnitude information |
| **Re-ranking** | RRF re-rank above; optional **cross-encoder** pass (`RERANK_ENABLED`, off by default; the `local` provider needs `sentence-transformers`, which is not a dependency; `cohere` needs a key) | Cross-encoder improves precision but is heavy to load — opt-in |
| **Context compression** | Similarity threshold (`SIMILARITY_THRESHOLD`, default 0.3; mentor KB 0.25) drops weak chunks before compose | Fewer tokens + less noise vs. risk of dropping a borderline-relevant chunk |
| **Hallucination prevention** | Source citations built from tool / retrieval results (`grounding.format_sources*`, numbered web sources; citations are formatted, not enforced — `compose_grounded_answer` exists but is not called), a **relevance gate** that refuses off-syllabus answers, and tool prompts that forbid guessing time-sensitive facts | Occasionally refuses a valid-but-thinly-retrieved query (precision over recall by design) |

> **HyDE** (`generate_hypothetical_document`) and **multi-query expansion** (`expand_queries`) exist in `retrieval.py` as opt-in upgrades; they add per-query LLM latency/cost and are gated behind the nightly eval harness rather than always-on.

### Agent Architecture

```mermaid
flowchart LR
    Q([Message]) --> S{{Supervisor — structured RouteDecision}}
    S -->|mentor| M[Mentor]
    S -->|planner| P[Planner]
    S -->|evaluator| E[Evaluator]
    S -->|current_affairs| C[Current Affairs]
    S -->|rag| R[CRAG subgraph — needs persist_key; not reachable over HTTP]
    M -.tools.-> T[web_search / knowledge_base_search]
    S -.default when unsure.-> M
```

NCERT, PYQ, Lecture and PDF chat do **not** go through the supervisor: their routes call plain feature functions in `src/agents/<feature>/graph.py` directly. The supervisor serves `POST /agent/chat/stream` (mentor chat) only. The only autonomous agent is the ReAct mentor.

- **Supervisor pattern.** A single LLM call returns a **structured `RouteDecision`** (Pydantic `Literal` over 5 routes). All subgraphs share one `AgentState`; only the supervisor is compiled with the checkpointer/store, so nested agents inherit memory. Callers may force a route by pre-setting `state['route']` when the UI already knows the target.
- **Agent routing.** Classify → dispatch to one specialist. Default to **mentor** when unsure (safe fallback). Complex mentor turns can escalate to a **plan-execute** graph (`is_complex`) when `PLAN_EXECUTE_ENABLED=true` (off by default).
- **Tool calling.** A ReAct-style loop (`build_tool_agent`) gives the *model* real tools (`web_search`, `knowledge_base_search`) and decides when/how to call them. Tool dispatch is a **pure, offline-tested function** isolated from the LLM.
- **Agent communication.** Via shared typed state — no ad-hoc message passing; structured params flow through `state['task_inputs']`, RAG adds `state['persist_key']`.
- **Failure recovery & retries.** Tool loop capped at `DEFAULT_MAX_TOOL_LOOPS = 3` (no infinite tool spirals); KB is a *bonus not a gate* — if retrieval returns nothing, the agent still answers timeless questions and only refuses on time-sensitive facts; provider fallback (`with_fallbacks`) plus the provider SDKs' own `max_retries` wrap flaky LLM calls.

### Background Processing Design

- **Why it exists.** PDF/lecture processing is slow; on a single free-tier worker it would block the request path and trip the 90s timeout. Jobs move that work off-request.
- **Lifecycle.** `POST /upload/process` → enqueue → return `job_id` immediately → thread-pool worker executes → status + result persisted in Postgres → client polls `GET /jobs/{id}` until `done`/`error`.
- **Queue architecture.** Thread-backed (`ThreadPoolExecutor(2)`, in-process), **no external broker**; job status and results are persisted in the DB, but the work itself is not durable — a job in flight when the process stops is lost and marked `error` on the next boot. Backend is selectable: `thread` / `inline` (tests) / `auto`.
- **Retry & failure handling.** On boot, `reap_stale_jobs` flips orphaned `queued`/`running` rows to `error`, so a client never polls a dead job forever — a definitive answer beats a stuck spinner.

### Scalability Design

- **Mostly externalised state.** Vectors live in Qdrant and user/auth/history/checkpoint state in Postgres, but some state is still in-process: the background-job thread pool, upload processing caches, and the LangGraph store when the DB is not Postgres.
- **Horizontal scaling.** Not yet safe to scale out freely: each process runs its own job pool, and the boot-time job reaper marks *all* queued/running jobs as `error`, including jobs owned by sibling workers. Run a single worker until jobs are made durable.
- **Redis rate limiting.** Upstash fixed-window counter is shared across instances (a per-process limiter would under-count behind a load balancer).
- **Caching.** Two-stage response cache (exact SHA-256 key + optional embedding-similarity match), **scoped per conversation** so a cached answer never leaks across users/threads.
- **Database scaling.** Pooled Postgres (`psycopg-pool`, Supabase/Neon 6543 pooler); SQLite only for local/tests. Cache + LITE routing cut load before it reaches the DB/LLM.

### Reliability Design

- **Circuit breakers.** Dependency-free breaker (CLOSED → OPEN → HALF_OPEN) wraps flaky upstreams (e.g. Redis REST): after N consecutive failures it fails fast for a cooldown, then probes once before closing. Prevents retry storms.
- **Provider fallback.** Gemini primary → Groq on 429/outage (`ENABLE_PROVIDER_FALLBACK`).
- **Timeout strategy.** Hard 90s per-request timeout so one stuck LLM/vector call can’t pin the single free-tier worker (= total outage without it).
- **Graceful degradation / fail-open.** Cache, Redis, Langfuse and Sentry degrade to a local/no-op fallback when unconfigured or failing (Qdrant has no working fallback — see Vector indexing) — the core chat path never breaks because an optional dependency is down. Email is the deliberate exception: in production, email flows without SMTP return 503 instead of pretending to succeed.
- **Error recovery.** Global exception handler returns a clean JSON envelope (never leaks a stack trace); stale-job reaping; startup token cleanup guarded so a DB hiccup can’t block boot.

### Observability Design

- **Structured logging** with request-scoped context (`logging_config.py`, `request_context.py`).
- **Request IDs** — inbound `X-Request-ID` honored or generated, threaded through every log line and returned on the response.
- **Tracing** — optional Langfuse traces on every LLM call (`observability.py`), flushed on shutdown.
- **Monitoring** — `HttpMetricsMiddleware` records latency/status/endpoint; an admin-only monitoring dashboard (`/monitoring`, `/cost`) surfaces them.
- **Alerting** — Sentry captures unhandled exceptions when `SENTRY_DSN` is set (no-op otherwise).

### Deployment Architecture

```mermaid
flowchart LR
    GH[GitHub] --> CI["CI Pipeline — GitHub Actions"]
    CI --> RB["Render — Backend (Python runtime + Alembic)"]
    CI --> VF["Vercel — Frontend SPA"]
    RB --> EXT{{External Services}}
    EXT --> PG[(PostgreSQL — Supabase)]
    EXT --> RD[(Redis — Upstash)]
    EXT --> QD[(Vector DB — Qdrant)]
    EXT --> LP[LLM Providers — Gemini / Groq]
    style CI fill:#2088FF,color:#fff
```

- **Backend (Render):** build `pip install uv && uv sync --frozen`; start `alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`; health `/health`; free plan, no persistent disk needed.
- **Frontend (Vercel):** build `npm run build`; output `dist/`; SPA rewrites to `/index.html`.
- **Container:** multi-stage, non-root Dockerfile targeting `src.api.main:app`, migrations before boot, container health check on `/health` (used for local/compose runs; the Render Blueprint uses the native Python runtime).
- **First redeployment scope:** NCERT PDFs are not included (the NCERT library shows no content until a storage option is chosen), and password-reset email stays unavailable until SMTP is configured. Details: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

### Security Design

- **JWT authentication.** Short-lived signed access tokens (`python-jose`); every protected router depends on `get_current_user`.
- **Token refresh flow.** Refresh tokens are **opaque random strings stored only as SHA-256 hashes** (a DB leak can’t be replayed). Each refresh **rotates** the token (old one revoked, new issued) so stolen-token reuse is detectable; `/auth/logout` revokes server-side — something a bare stateless JWT cannot do.
- **Rate limiting.** Global per-IP limit. A stricter auth-endpoint limit is configured (`AUTH_RATE_LIMIT_*`, `10 / 300s`) but not yet applied anywhere.
- **Secret management.** All secrets via env (`pydantic-settings`); `.env` git-ignored; Render Blueprint secrets are `sync: false`; sensitive values kept out of logs (`secret_utils.py`). CORS is an explicit allowlist.
- **Security headers + prompt safety.** Hardening headers on every response (relaxed CSP only on `/docs`); untrusted retrieved/uploaded text is sanitised + wrapped before reaching the model (`prompt_safety.py`); per-user ownership checks on history (`test_security_ownership.py`).

---

## 3. Engineering Decisions

<details open>
<summary><b>Why FastAPI?</b></summary>

**Problem:** async I/O to LLMs/vector DBs + typed request/response contracts + auto docs.<br>
**Alternatives:** Flask (sync-first, manual validation), Django (heavy for an API-only service), Node/Express (would split the AI stack across languages).<br>
**Decision:** FastAPI + Pydantic v2.<br>
**Tradeoff:** Async correctness is on me (blocking calls must be offloaded); smaller ecosystem than Django for batteries-included features.
</details>

<details>
<summary><b>Why LangGraph (not a bare agent loop / CrewAI)?</b></summary>

**Problem:** deterministic, inspectable routing and tool-calling control flow with shared memory + checkpointing.<br>
**Alternatives:** hand-rolled prompt chains (no state/checkpoint), CrewAI/AutoGen (more opinionated, harder to make deterministic and offline-testable).<br>
**Decision:** LangGraph supervisor + nested subgraphs over one shared `AgentState`, Postgres checkpointer.<br>
**Tradeoff:** learning curve + framework coupling; in return, resumable state and a routing graph I can unit-test.
</details>

<details>
<summary><b>Why a supervisor architecture?</b></summary>

**Problem:** one mega-prompt can’t grade answers, plan schedules, and do RAG well simultaneously.<br>
**Alternatives:** single prompt with mode flags (brittle), keyword routing (misses intent).<br>
**Decision:** LLM supervisor returns a structured `RouteDecision`; defaults to mentor when unsure.<br>
**Tradeoff:** one extra LLM hop per request (mitigated by using the cheap LITE model for routing).
</details>

<details>
<summary><b>Why vector search + RRF (not linear hybrid)?</b></summary>

**Problem:** grounding answers in source text; a linear blend of cosine + lexical scores was brittle across corpora.<br>
**Alternatives:** pure dense (misses exact terms), pure lexical (misses paraphrase), tuned linear weights (drift per corpus).<br>
**Decision:** dense retrieval, then the dense and lexical rankings of those candidates fused by **Reciprocal Rank Fusion** (rank-based, scale-independent).<br>
**Tradeoff:** discards absolute-score magnitude; the lexical signal can only re-order dense hits, not add new ones.
</details>

<details>
<summary><b>Why Redis (Upstash)?</b></summary>

**Problem:** rate limiting + response cache must be correct across multiple instances.<br>
**Alternatives:** in-process counters (wrong behind a load balancer), a full self-hosted Redis (infra cost on free tier).<br>
**Decision:** Upstash Redis over REST (no extra server), wrapped in a circuit breaker, with an in-process fallback.<br>
**Tradeoff:** approximate limits during fallback; REST latency per op vs. running my own Redis.
</details>

<details>
<summary><b>Why background workers?</b></summary>

**Problem:** PDF/lecture processing exceeds a safe request budget on one worker.<br>
**Alternatives:** block the request (times out), Celery/RQ + broker (infra + cost).<br>
**Decision:** thread-backed queue with DB-persisted status — zero extra infra.<br>
**Tradeoff:** limited to in-process concurrency (fine for free tier); not a distributed broker, and in-flight jobs do not survive a restart.
</details>

<details>
<summary><b>Why streaming responses?</b></summary>

**Problem:** multi-second LLM generations feel broken if the UI waits for the full answer.<br>
**Alternatives:** block-and-return (poor UX), polling (complex).<br>
**Decision:** `StreamingResponse` (token-by-token) for chat routes; `/sync` variants return Pydantic-validated structured output for programmatic use.<br>
**Tradeoff:** streaming + strict output schemas don’t mix, hence two endpoint shapes to maintain.
</details>

---

## 4. Production Features

| Feature | Where | One-line proof |
|---|---|---|
| **Background workers** | `core/job_queue.py` | In-process thread pool, DB-persisted job status, stale-job reaping on boot |
| **Circuit breakers** | `core/circuit_breaker.py` | CLOSED/OPEN/HALF_OPEN, thread-safe, fail-fast |
| **Distributed rate limiting** | `api/rate_limit_core.py` | Upstash window + in-proc sliding-window fallback |
| **Request tracing** | `api/request_id.py` | Correlation ID on every log line + response |
| **Structured logging** | `core/logging_config.py` | Request-scoped context formatters |
| **CI/CD** | `.github/workflows/ci.yml` | lint (non-blocking) → compile → tests → build → nightly LLM-eval |
| **Error recovery** | `api/main.py` | Global JSON exception handler, 90s timeout, fail-open deps |

---

## 5. Performance & Reliability Metrics

> Only measured, reproducible numbers. No latency figures are asserted — the backend runs on Render’s free plan (sleeps on idle), so cold-start/latency vary by tier. Reproduce each with the command shown.

| Metric | Value | Reproduce |
|---|---|---|
| Automated tests | **930 tests / 64 modules**, fully offline (full run 2026-11-02) | `uv run pytest -q` |
| Backend size | **15,455 LOC / 109 modules** | `find src -name '*.py' \| xargs wc -l` |
| Frontend size | **6,243 LOC / 40 files** | `find frontend/src -name '*.ts*' \| xargs wc -l` |
| Initial JS+CSS load | **244 KB raw ≈ 82 KB gzip** | `frontend/dist/assets/index-*` |
| Built frontend output | **920 KB**, code-split into 17 chunks | `du -sh frontend/dist` |
| DB migrations | 4 Alembic revisions | `ls migrations/versions` |
| CI quality gate | lint (ruff + prettier, currently `continue-on-error`) + `compileall` + pytest + frontend build; nightly faithfulness gate `≥0.9`, relevancy `≥0.7`, precision `≥0.6` | `.github/workflows/ci.yml` |
| Reliability gates | 90s request timeout · 3-loop tool cap · circuit breaker · provider fallback | source-verifiable |

---

## Failures & Lessons Learned

- **Embedding dimension mismatch (3072 vs 768).** `langchain-google-genai` silently ignores `output_dimensionality` as a constructor arg, so vectors stayed 3072-dim and mismatched a 768-dim Qdrant collection. **Fix:** a wrapper that pins dims per-call and, as a guarantee, Matryoshka-truncates + L2-normalizes every vector. **Lesson:** never trust a config knob you haven’t asserted on the actual output shape.
- **Single-worker outage risk.** Without a hard request timeout, one stuck LLM/vector call pins the only free-tier worker → total outage. **Lesson:** on constrained infra, timeouts and circuit breakers aren’t optional polish — they’re availability.
- **Email-verification foot-gun.** `REQUIRE_EMAIL_VERIFICATION=true` without SMTP configured would lock users out. Now, outside production verification is auto-disabled with a warning; in production the backend refuses to start in that state, and email flows (e.g. password reset) return 503 rather than claiming success. Links and tokens are never logged. CI pins dummy SMTP so the auth flow is actually exercised. **Lesson:** fail-open on the *right* thing, and make tests reflect production toggles.
- **UI bug caught by automation (documented, not hidden).** During Playwright screenshot capture, the Planner’s “Generated plan” view crashed into its ErrorBoundary in dark mode: `TypeError: Cannot read properties of null (reading 'open')` at a `<details onToggle>` reading `e.currentTarget.open` during mount. Deterministic and theme-agnostic (dark timing just exposes it). **Fix:** guard `e.currentTarget` before reading `.open`. **Lesson:** end-to-end automation surfaces real latent bugs unit tests miss — report them, don’t paper over them.

---

## Screenshots

> Images below use repo-relative paths (`screenshots/*.png`). They render on GitHub and when this folder is kept intact. **This README is shipped alongside the `screenshots/` folder in the download bundle**, so open it from the unzipped folder (not the standalone `.md`) for images to load.

<table>
<tr>
<td width="33%"><img src="screenshots/dashboard.png" alt="Dashboard"/><div align="center"><sub><b>Dashboard</b></sub></div></td>
<td width="33%"><img src="screenshots/mentor-chat.png" alt="Mentor"/><div align="center"><sub><b>Mentor</b></sub></div></td>
<td width="33%"><img src="screenshots/planner.png" alt="Planner"/><div align="center"><sub><b>Planner</b></sub></div></td>
</tr>
<tr>
<td width="33%"><img src="screenshots/evaluator.png" alt="Evaluator"/><div align="center"><sub><b>Evaluator</b></sub></div></td>
<td width="33%"><img src="screenshots/upload-rag.png" alt="RAG Upload"/><div align="center"><sub><b>RAG Upload</b></sub></div></td>
<td width="33%"><img src="screenshots/current-affairs.png" alt="Current Affairs"/><div align="center"><sub><b>Current Affairs</b></sub></div></td>
</tr>
</table>

<sub>Full light + dark gallery and mapping: <a href="screenshots/README_SCREENSHOTS.md">screenshots/README_SCREENSHOTS.md</a></sub>

---

## 6. For Reviewers & Recruiters

**What this project demonstrates**

| Competency | Concrete evidence in this repo |
|---|---|
| **System Design** | Layered architecture, deliberate middleware ordering, externalised vector/user/checkpoint state |
| **Production AI Engineering** | Provider fallback, model routing (cost/quality), semantic cache, CI eval gate |
| **Agentic AI** | LLM-routed supervisor over 5 routes, ReAct tool-calling mentor with a hard loop cap |
| **LangGraph** | Shared `AgentState`, checkpointer, nested subgraphs, opt-in plan-execute escalation |
| **RAG Systems** | Dense retrieval, RRF re-ranking with a lexical signal, opt-in cross-encoder rerank, source citations |
| **Distributed Systems** | Distributed rate limiting, circuit breakers, persisted job queue, fail-open deps |
| **FastAPI** | 16 registered route modules, DI auth, streaming + structured endpoints, Pydantic contracts |
| **Observability** | Structured logs, request-ID tracing, Langfuse, Sentry, metrics dashboard |
| **DevOps** | Multi-stage non-root Docker, uv-locked builds, Render Blueprint, GitHub Actions |
| **Reliability Engineering** | Timeouts, retries, graceful degradation, stale-job reaping |
| **Full-Stack Development** | React 18 + TS SPA, 40 files / 6.2k LOC, code-split bundle |

**The throughline:** every feature is paired with an *operational* decision — fail-open fallbacks, cost controls, honest measurement, and documented failures — chosen for real free-tier constraints. This is a system owned from **architecture → implementation → deployment → operations**.

---

<div align="center">

**Author — Vishal Shivhare** · GenAI / Backend Engineer

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Vishal_Shivhare-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/vishal-shivhare-562508243/)

License: **MIT** — see [LICENSE](LICENSE).

</div>
