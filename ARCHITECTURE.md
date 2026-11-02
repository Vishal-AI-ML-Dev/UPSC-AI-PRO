# UPSC Agentic AI — Architecture

A RAG backend for UPSC exam preparation, built on FastAPI + LangGraph. Mentor
chat (`POST /agent/chat/stream`) goes through a LangGraph supervisor: one
structured LLM routing call per message to one of 4 routes (mentor, planner,
evaluator, current_affairs). The mentor route is a ReAct tool-calling agent — the
only autonomous agent. The other features (NCERT, PYQ, lecture, PDF chat, and
the planner/evaluator/current-affairs pages) call plain feature functions in
`src/agents/<feature>/graph.py` directly from their routes. Retrieval is dense
vector search whose candidates are re-ranked by RRF over dense + lexical
rankings; mentor answers carry formatted source citations.

---

## 1. High-level flow

```
HTTP request
  -> src/api/main.py            (FastAPI app = `app`)
  -> src/api/routes/*.py        (per-feature endpoints, auth, rate limits)
  -> src/graph/*.py             (LangGraph supervisor + tools; mentor chat only)
  -> src/agents/<feature>/graph.py (plain feature functions + prompts; not LangGraph)
  -> src/core/*.py              (LLM, retrieval, grounding, DB, security, ...)
  -> src/schemas.py             (Pydantic request/response contracts)
```

Entry point: `src/api/main.py` exposes `app`. Run locally with `uv`:

```bash
uv run uvicorn src.api.main:app --reload
```

---

## 2. Directory map

### `src/api/` — HTTP layer
- `main.py` — FastAPI app factory (`app`), router registration, middleware.
- `deps.py` — shared dependencies (auth user, DB session).
- `rate_limit.py`, `upload_limit.py` — request/upload throttling.
- `routes/` — one module per feature:
  - `auth.py` — login / refresh / password reset.
  - `chat.py` — mentor chat entry (`/agent/chat/stream`, via the supervisor).
  - `evaluator.py`, `planner.py`, `pyq.py`, `ncert.py`,
    `lecture.py`, `current_affairs.py`, `upload.py` — feature endpoints.
  - `mentor.py` — legacy mentor router; **not registered** in `main.py` (dead code).
    Most expose a streaming route plus a `/sync` route that also returns
    structured (Pydantic-validated) data.
  - `history.py`, `feedback.py` — conversation history & user feedback.

### `src/agents/` — feature logic
Each feature is a self-contained package: `graph.py` (plain Python functions,
despite the name — not a LangGraph graph) + `prompts.py` (prompt templates).
Features: `mentor`, `evaluator`, `planner`, `pyq`, `ncert`, `lecture`,
`current_affairs`, `upload`. (`upload/upload.py` is an unmounted duplicate
router — dead code.)
- `evaluator/` — grades answers/mains; output parsed into structured schemas
  by `src/core/eval_parse.py`.
- `planner/` — builds study plans; `constants.py` holds domain constants,
  timeline metadata is computed by `src/core/plan_timeline.py`.
- `current_affairs/` — includes `ingest.py` / `monthly_ingest.py` for
  loading current-affairs sources into the vector store.

### `src/graph/` — orchestration
- `supervisor.py` — one structured LLM routing call per message to one of 4
  routes (mentor chat only). A route saved in the checkpoint never decides the
  next message (Phase 05, ADR-014). Optional plan-execute escalation
  (`plan_execute.py`) is off by default (`PLAN_EXECUTE_ENABLED`).
- `app_graph.py` — top-level assembled graph.
- `agent_subgraphs.py` — wraps the planner/evaluator/current-affairs feature
  functions as supervisor subgraphs; inputs are parsed from the chat message.
  HITL (`HITL_ENABLED`, off) is not supported by mentor chat (no resume path).
- `rag_graph.py` — corrective-RAG pipeline. Not a supervisor route (removed in
  Phase 05); it only runs in `src/eval/llm_eval.py`.
- `tools.py` — tool-calling agent (per-tier model resolution, tool loop,
  route description).
- `mentor_graph.py`, `memory.py`, `profile.py`, `state.py` — mentor graph,
  conversation memory, user profile extraction, shared graph state.

### `src/core/` — shared services
- `llm.py` — LLM providers: `get_llm()`, `get_fast_llm()`,
  `get_llm_for_tier(tier)`, `reset_llm()`.
- `model_router.py` — LITE vs STRONG tier routing heuristics.
- `retrieval.py` — query rewriting, lexical/concept scoring, RRF fusion,
  reranking.
- `vector_store.py` — vector DB access (Qdrant; required). The coded Chroma
  fallback needs `langchain_chroma`, which is not installed.
- `grounding.py` — citation extraction, source formatting, confidence.
- `eval_parse.py` — parse evaluator markdown into `AnswerEvaluation` /
  `MainsEvaluation`.
- `plan_timeline.py` — attempt-year / months-left / timeline messaging ->
  `StudyPlanMeta`.
- `prompt_safety.py` — sanitize & wrap untrusted content.
- `db.py`, `models.py` — SQLAlchemy engine/session and ORM table models
  (distinct from the Pydantic API schemas in `src/schemas.py`).
- `users.py`, `security.py`, `history.py`, `secret_utils.py`,
  `reset_tokens.py`, `verification_tokens.py`, `email_utils.py` — auth,
  users, secrets, tokens, email.
- `config.py`, `logging_config.py`, `observability.py` — settings, logging,
  tracing (Langfuse).
- `mentor_kb.py`, `study_aids.py` — mentor knowledge base & study helpers.

### `src/eval/` — offline evaluation harness
- `llm_eval.py` — LLM-as-judge eval run (`run_llm_eval`).
- `retrieval_eval.py` — retrieval quality metrics.
- `gates.py` — strict quality gates (faithfulness/relevancy/precision).
- `eval_dataset.json` — eval fixtures; `*_report.md` — generated reports.

### `src/schemas.py` — API contracts
Single module holding all Pydantic request/response models
(`ChatRequest`, `PlannerRequest`/`PlannerResponse`, `MentorRequest`,
`AnswerEvaluation`, `MainsEvaluation`, `StudyPlanMeta`, ...).

> Note: This was previously `src/models/` and was renamed to `src/schemas.py`
> to avoid confusion with `src/core/models.py` (SQLAlchemy ORM tables).

---

## 3. Retrieval pipeline (RAG)

1. **Query rewriting / expansion** (`retrieval.rewrite_query`).
2. **Dense retrieval** — vector similarity search in Qdrant. There is no
   separate lexical index.
3. **Lexical scoring + RRF** (`rerank_scored_documents`,
   `reciprocal_rank_fusion`) — the dense candidates are scored by concept
   coverage, and the dense and lexical rankings of those same candidates are
   fused by RRF.
4. **Optional cross-encoder** (`cross_encoder_rerank`) — off by default
   (`RERANK_ENABLED`); the `local` provider needs `sentence-transformers`,
   which is not a dependency.
5. **Citations** — source lists and a trust/confidence note are formatted from
   retrieval / tool results (`grounding.format_sources*`, `format_trust_note`).
   `grounding.compose_grounded_answer` exists but is not called, so citations
   are not enforced.

---

## 4. Model routing

`src/core/model_router.py` picks a tier per request:
- **LITE** — greetings, short/simple queries.
- **STRONG** — reasoning cues, long queries, tool-requiring volatile lookups.
`describe_route()` returns the chosen tier + human-readable reason; the tool
agent resolves per-tier models via `get_llm_for_tier()`.

---

## 5. Quality gates & CI

- Unit/integration tests in `tests/` (run with `uv run pytest -q`).
- Offline LLM eval gated in CI (`src/eval/llm_eval.py --gate 0.9`),
  scheduled + artifact-uploaded via `.github/workflows/ci.yml`.
- Strict gates: faithfulness 0.9, relevancy 0.7, context precision 0.6.

---

## 6. Constraints & deployment

- Designed to run on modest hardware (code-only + cloud free tiers); no local
  Docker/K8s/observability stack required for development.
- Cloud services (free tier): Qdrant (vectors), Postgres (state/checkpoints),
  Google Gemini / Groq (LLMs), Tavily (web search), Langfuse (tracing).
- Deploy config: `render.yaml`. Frontend lives in `frontend/` (Vite + React SPA).
