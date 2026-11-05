# Project highlights

The strongest engineering work in the repository, each split into **what is implemented** and **what has actually been measured**.
Implementation is not impact: a feature that exists in code is IMPLEMENTED; a number is only quoted where a recorded measurement exists.
Terminology and labels: [ENGINEERING_METRICS.md](ENGINEERING_METRICS.md). Interview-ready, but every claim below is checkable in the cited file.

## 1. Application architecture
- **Implemented:** layered FastAPI backend (`routes` → `agents`/`graph` → `core`), React SPA with a single API client, 54 REST operations, deliberate middleware order
  (request id outermost), plain-function workflows plus one LangGraph graph. Decisions are recorded as ADRs (`docs/DECISIONS.md`).
- **Measured:** structure counts only (113 backend modules, 44 frontend files). No runtime/architecture performance claims beyond the synthetic benchmarks.
- See [ARCHITECTURE.md](ARCHITECTURE.md).

## 2. RAG implementation
- **Implemented:** Gemini embeddings pinned to 768 dimensions (with a fix for a silently ignored SDK parameter), 1,000/150 chunking, Qdrant collection per owner-scoped key, dense search,
  lexical re-scoring of the dense candidates, Reciprocal Rank Fusion (k = 60), score thresholds, a two-stage response cache. Cross-encoder reranking, HyDE and multi-query exist but are **disabled**.
  There is no BM25 index.
- **Measured (offline, small, AI-labelled):** pooled Recall@k 0.988, MRR@k 0.935 across 97 queries; RRF re-ordering gave a marginal NDCG gain (+0.030, CI barely excluding 0); thresholds filtered
  none of 13 unanswerable queries. The honest conclusion recorded in ADR-015 was to change nothing in production.
- See [RAG_AND_RETRIEVAL.md](RAG_AND_RETRIEVAL.md).

## 3. LangGraph routing
- **Implemented:** `StateGraph` supervisor with a structured `RouteDecision` over four specialists, nested subgraphs, a ReAct mentor agent with two tools and a three-round cap,
  Postgres checkpointer and store.
- **Measured:** an offline benchmark with stubbed models showed a stale-route bug (160/200 misrouted) and its fix (0/200). Real routing accuracy and latency are UNVERIFIED.
- See [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md).

## 4. API and backend engineering
- **Implemented:** JWT-protected routers by default, Pydantic limits on free text, byte-counted upload limits, streaming endpoints with a session guard, fixed error messages,
  request ids, per-IP and auth-specific rate limits, versioned Alembic migrations (six) with a PostgreSQL advisory lock for concurrent runners.
  Moving 18 blocking handlers off the event loop was a performance fix.
- **Measured (synthetic, local):** blocking-LLM scenario 1.38–1.62 → 19.1–20.0 requests/s (≈ 13×), `/health` p99 from ~5 s to ~8–22 ms. Not production numbers.
- See [API_OVERVIEW.md](API_OVERVIEW.md).

## 5. User data isolation
- **Implemented:** user-id filters on every owned DB query; owner-scoped vector keys built server-side; model-facing tool restricted to the mentor KB; LangGraph thread ids and cache keys
  hashed with the user id. A cross-tenant weakness found in the baseline audit was fixed in Phase 02 (ADR-007).
- **Measured:** covered by dedicated ownership/IDOR tests (e.g. `test_security_ownership.py`, `test_chat_isolation.py`). No external penetration test.
- See [SECURITY.md](SECURITY.md).

## 6. LLM reliability
- **Implemented:** Gemini → Groq fallback, 45-second per-call timeout that ends the request, one retry, bounded history window (current turn + 6 earlier turns), output-token cap, structured
  failure notices instead of silent errors, score parsing clamped to valid ranges.
- **Measured:** behaviour under scripted failures only. Real provider failure rates and fallback frequency are UNVERIFIED.

## 7. Background job reliability
- **Implemented:** DB-persisted job status, thread-pool execution, heartbeat lease, conditional status transitions (late results cannot overwrite a failure), transient-only retries, deadline, graceful shutdown.
  Work is **not** resumable after a crash.
- **Measured (offline, multi-process, SQLite, scaled-down timings):** 0 false reaps across 14–16 sibling recoveries; dead-worker jobs recovered in about one lease length. PostgreSQL and `WEB_CONCURRENCY > 1`
  under HTTP load are UNVERIFIED. No Redis/RQ/Celery is used.

## 8. Prompt-injection defence
- **Implemented:** `harden_untrusted` fencing with marker neutralisation (nested, Unicode and decoy variants), an UPSC-only deterministic scope gate, scope rules in system prompts, allow-listed fields.
- **Measured:** offline T1 tier — 21 failures on pre-fix code → 0 after fixes (60 cases, 1 known gap); T2 72/72 with scripted models. T3 (real model) never run.

## 9. CI and automated testing
- **Implemented:** GitHub Actions runs `pytest -q`, compile check, frontend type-check/build on every push and PR; nightly/manual judge gate; offline-isolated benchmark harnesses.
- **Measured:** 1,847 cases collected on the author's working tree (includes unpublished Phase 09 tests; the published branch has slightly fewer); last recorded full runs `1765 passed, 2 skipped` (Phase 08 close) and `1790 passed, 2 skipped` (dirty Phase 09 tree). Lint and prettier are non-blocking; the live judge gate
  targets a non-production graph; the live smoke job is disabled.
- See [TESTING.md](TESTING.md).

## 10. Cloud deployment
- **Implemented:** Render Blueprint (`uv`-locked build, migrations before boot, health check), Vercel SPA, non-root multi-stage Dockerfile, Supabase PostgreSQL, Qdrant Cloud, Cloudflare R2.
- **Measured:** release of migrations 0005/0006 to production owner-reported; migrations were rehearsed on disposable PostgreSQL 15/17. No uptime, cold-start or latency figures. No production backup has been taken under the runbook.
- See [DEPLOYMENT.md](DEPLOYMENT.md).

## What I would say is *not* a highlight (and why that matters)
Active monitoring, production latency, user traffic, hallucination reduction and real-model safety are all unmeasured. Saying so plainly is deliberate: the repository prefers a documented gap to an unsupported number.
