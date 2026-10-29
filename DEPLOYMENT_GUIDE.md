# UPSC AI Pro — Free-Tier Deployment Guide

Deploy the stack on free tiers. **Status: not deployed yet** — this guide
describes the intended first deployment. All URLs below are placeholders
(`<...>`); replace them with the real values once each service exists.

**Before you start:** finish the credential rotation and old-account cleanup
(old GitHub/Render/Vercel access revoked, fresh provider keys). Never reuse a
key or `JWT_SECRET` from the old deployment.

**Target architecture**

| Layer | Service (free tier) | Notes |
|---|---|---|
| Frontend (React/Vite SPA) | **Vercel** | `frontend/vercel.json` (SPA rewrites) |
| Backend (FastAPI + LangGraph) | **Render** (free web, Python runtime) | `render.yaml` Blueprint; stateless, no disk |
| Database | **Supabase Postgres** | Users, sessions, history, LangGraph checkpoints |
| Vector DB | **Qdrant Cloud** | Mentor KB + uploads; collections created by the app (768-dim) |
| LLM | **Google Gemini** (primary) + **Groq** (fallback) | Gemini key also used for embeddings |
| Redis (cache / rate limit) | **Upstash Redis** | Optional; single worker works without it |
| Web search | Tavily or built-in DuckDuckGo | Optional |
| Observability | Langfuse + Sentry | Optional |
| NCERT PDFs | **Not included in the first deployment** | See "First-deployment scope" |

## First-deployment scope (confirmed decisions)

- **NCERT PDFs are deferred.** They are excluded from Git (`data/ncert/`) and
  Render's free disk is ephemeral. Leave `NCERT_DATA_DIR` unset: the NCERT
  library then shows no content; the rest of the app is unaffected. A storage
  option (persistent disk or object storage) will be chosen later.
- **Password reset is unavailable until SMTP is configured.** In production,
  forgot-password returns **503** ("Email delivery is not configured") instead
  of sending mail; nothing is logged. Keep `REQUIRE_EMAIL_VERIFICATION=false`
  (already set in `render.yaml`): setting it to `true` without SMTP makes the
  backend **refuse to start**. Registration and login work without SMTP.
- **Still undecided:** LLM model policy (`LLM_MODEL` / `LLM_FAST_MODEL` use code
  defaults) and web search (`WEB_SEARCH_ENABLED` defaults to `true`).

---

## Step 0 — Accounts
Google AI Studio (Gemini key), Groq, Supabase, Qdrant Cloud, Render, Vercel,
GitHub (new repository). Optional: Upstash, Tavily, Langfuse, Sentry.
Enable 2FA on each. Prefer email/Google login over "Sign in with GitHub" from
any old account.

## Step 1 — Database (Supabase)
1. New project, region close to the backend (`render.yaml` uses Singapore).
2. Connect → copy the **Session pooler** connection string shown in the
   dashboard (use it rather than the direct connection; Render may not reach
   Supabase's IPv6-only direct host). Append `?sslmode=require` if absent.
3. Save it as `DATABASE_URL` (secret). Migrations run automatically on deploy.

Verified locally only: migrations `0001`→`0004`, the session flow and the
Postgres checkpointer were tested against a local PostgreSQL 16 container. The
Supabase pooler connection itself has not been tested yet.

## Step 2 — Vector DB (Qdrant Cloud)
Create a free cluster → copy `QDRANT_URL` (`https://<cluster-host>:6333`) and
`QDRANT_API_KEY`. Do not set `QDRANT_PATH` in production (single-process only;
setting both fails startup).

## Step 3 — Keys and secrets
- `GOOGLE_API_KEY` (required), `GROQ_API_KEY` (recommended fallback).
- `JWT_SECRET` (≥ 32 chars; production refuses to start otherwise). Generate it
  locally and do not paste it into chats or files:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- Optional: `TAVILY_API_KEY`, `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`,
  `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`, `SENTRY_DSN`.

## Step 4 — Backend (Render)
1. Push the repository to the new GitHub account (`<github-repo-url>`).
2. Render → **New** → **Blueprint** → select the repo (reads `render.yaml`;
   service `upsc-ai-pro-api` — rename in `render.yaml` if the name is taken).
3. Enter every `sync: false` value: `DATABASE_URL`, `JWT_SECRET`,
   `GOOGLE_API_KEY`, `GROQ_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`,
   `TAVILY_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
   `CORS_ORIGINS`, `FRONTEND_URL`, `ADMIN_EMAILS` (leave optional ones empty if
   unused). `ENV=production` and `REQUIRE_EMAIL_VERIFICATION=false` are already
   in `render.yaml`.
   - `CORS_ORIGINS=https://<your-frontend>.vercel.app` — exact origin,
     comma-separated for several, **no trailing slash** (a trailing slash is not
     stripped and will not match the browser's Origin). A JSON list also works.
     `*` is dropped.
   - `FRONTEND_URL=https://<your-frontend>.vercel.app` (no trailing slash).
   - `ADMIN_EMAILS=<admin-email>` (comma-separated; empty = no admins).
   - If the Vercel URL is not known yet, use a placeholder and update both
     values after Step 5.
4. Deploy. The start command runs `alembic upgrade head`, then uvicorn on
   `$PORT`. Check `https://<your-backend>.onrender.com/health` returns
   `"status": "healthy"`.

Free plan limits: the service sleeps after ~15 min idle (slow first request)
and has 512 MB RAM. The memory footprint has not been measured yet.

## Step 5 — Frontend (Vercel)
1. **Add New Project** → import the repo → **Root Directory = `frontend`**.
2. Environment variable (build time, public — never put a secret in `VITE_*`):
   `VITE_API_BASE=https://<your-backend>.onrender.com/api/v1`.
   A production build without it shows "Backend API URL is not configured".
3. Deploy, then set the real Vercel origin in Render's `CORS_ORIGINS` and
   `FRONTEND_URL` and redeploy the backend.

## Step 6 — Seed the Mentor knowledge base (optional, live calls)
`scripts/ingest_mentor_kb.py` (and `scripts/ingest_topper_md.py`) call the
**Gemini embedding API** and fetch YouTube transcripts, so they use quota and
network. Run them from the project root with production `QDRANT_*` values in
your local `.env` only when you intend to, e.g.
`uv run python scripts/ingest_mentor_kb.py`.

## Step 7 — CI/CD
- `.github/workflows/ci.yml` runs lint, tests and the frontend build without
  secrets. The scheduled/manual `llm-eval` job needs repository secrets
  (`GOOGLE_API_KEY`, `GROQ_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`,
  `DATABASE_URL`, `LANGFUSE_*`); add them only if you want it, and never point
  it at the production database. The `smoke-live` job is disabled.
- Deploys: use Render's and Vercel's built-in Git auto-deploy. No deploy-hook
  workflow is included in this repository.
- Keep-awake: optional external uptime monitor on `/health` (no workflow is
  included).

## Before calling it live
- Register, log in, refresh and log out work in the browser (no CORS errors).
- One Mentor question answers (live Gemini call).
- Planner and Evaluator each answer once.
- Password reset shows the "not configured" message (expected until SMTP).

---

## Local production-like run (Docker Compose)
```bash
cp .env.example .env   # fill in keys; set JWT_SECRET (>= 32 chars)
docker compose up --build
# API http://localhost:8000/health   •   Web http://localhost:8080
```
Compose runs the backend with `ENV=production`, so set
`REQUIRE_EMAIL_VERIFICATION=false` in `.env` (or configure SMTP), otherwise the
backend refuses to start. Compose publishes Postgres on host port 5432; stop any
other local Postgres using that port first.

## Health checks
- `GET /health` → liveness/readiness (Render, Docker HEALTHCHECK, uptime monitors).
- `GET /` → metadata. `GET /docs` → Swagger.
