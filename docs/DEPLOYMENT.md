# Deployment

How UPSC AI Pro is built and deployed. Everything here describes **repository configuration** (`render.yaml`, `Dockerfile`,
`frontend/vercel.json`, CI). Live service settings (dashboard values, secrets, auto-deploy toggles) are not in the repository;
where a statement depends on them it is marked UNVERIFIED. No secret values appear in this document or should be added to it.

The older [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) is the step-by-step first-deploy walkthrough (its "not deployed yet"
banner pre-dates the live deployment). This page is the current summary.

## Topology
| Layer | Service | Source of truth | Status |
|---|---|---|---|
| Frontend (React SPA) | **Vercel** | `frontend/vercel.json` (`npm run build` → `dist`, SPA rewrite to `/index.html`) | Live URL supplied by owner: `https://upsc-ai-pro.vercel.app/` |
| Backend (FastAPI) | **Render** web service, free plan, Singapore | `render.yaml` | Live URL supplied by owner: `https://upsc-ai-pro-puih.onrender.com` |
| Database | **Supabase PostgreSQL** (session pooler) | `DATABASE_URL` (secret) | In use per owner report; revision `0006_evaluation_attempts` confirmed by the owner |
| Vector DB | **Qdrant Cloud** | `QDRANT_URL`, `QDRANT_API_KEY` (secrets) | Required |
| Object storage | **Cloudflare R2** private bucket | `R2_*` (secrets) | Holds NCERT PDFs only |
| LLMs | Google Gemini (primary), Groq (fallback) | `GOOGLE_API_KEY`, `GROQ_API_KEY` | Required (at least one) |
| Redis | **Upstash Redis** (optional) | `UPSTASH_REDIS_REST_*` | Code exists; **not set in `render.yaml`**, so rate-limit sharing and the response cache are **UNVERIFIED in production** |
| Observability | Langfuse, Sentry (optional) | `LANGFUSE_*`, `SENTRY_DSN` | Sentry not activated; Langfuse activation UNVERIFIED |

## Render backend (`render.yaml`)
- Runtime: Python `3.13.13`; plan `free`; `branch: main`.
- Build: `pip install uv && uv sync --frozen`.
- Start: `uv run alembic upgrade head && uv run uvicorn src.api.main:app --host 0.0.0.0 --port $PORT --proxy-headers`.
- Health check path: `/health` (a static liveness response; it does not check the database or Qdrant).
- Non-secret variables set in the file: `ENV=production`, `FORWARDED_ALLOW_IPS` (private ranges only), `MAX_UPLOAD_MB=50`,
  `SIMILARITY_THRESHOLD=0.3`, `MENTOR_KB_THRESHOLD=0.25`, `ENABLE_PROVIDER_FALLBACK=true`, `REQUIRE_EMAIL_VERIFICATION=false`,
  `LANGFUSE_HOST`.
- Secret variables declared `sync: false` (set in the dashboard): Qdrant, R2, Gemini, Groq, `DATABASE_URL`, `JWT_SECRET`, Tavily,
  Langfuse keys, `CORS_ORIGINS`, `FRONTEND_URL`, `ADMIN_EMAILS`.
- Hostname: the Blueprint names the service `upsc-ai-pro-api`; Render adds a suffix when assigning the public hostname, which is
  consistent with the `…-puih…` host the owner reported.

## Docker (`Dockerfile`, `docker-compose.yml`)
- Multi-stage build on `python:3.13-slim`, runs as a **non-root** user, `HEALTHCHECK` on `/health`.
- Command: `alembic upgrade head && uvicorn src.api.main:app … --workers ${WEB_CONCURRENCY:-1} --proxy-headers`. Keep one worker unless the
  job-queue and rate-limit caveats in [ARCHITECTURE.md](ARCHITECTURE.md) are addressed.
- `docker-compose.yml` is for **local production-like runs** (Postgres 16, Qdrant, Redis, backend, nginx frontend). Known hygiene
  gaps: unpinned `qdrant:latest`, default dev credentials, published ports, and a Redis service the app does not need.
- The Render Blueprint uses the native Python runtime; the Docker image is not what Render runs.

## Frontend (Vercel)
- Build `npm run build` (TypeScript check + Vite), output `dist`. The API base is **compiled in** from `VITE_API_BASE`; a production build
  without it fails with a clear configuration error and has no remote fallback (`frontend/src/lib/config.ts`).
- The backend `CORS_ORIGINS` must include the Vercel origin.

## Environment variables
Names and meanings live in `src/core/config.py` and the placeholder file `.env.example`. **Do not copy values into documentation, issues or
chat.** Groups: runtime (`ENV`, `PORT`), database, vector store, LLM, embeddings, auth (`JWT_SECRET`, token lifetimes), CORS, limits,
cache/Redis, observability, NCERT/R2, jobs. Production requires `JWT_SECRET`; the app refuses to boot in production without a strong one.

## Deployment and migration sequence
1. Merge to `main` after the **tests** and **frontend** CI jobs pass.
2. Render builds and starts; the start command runs `alembic upgrade head` (concurrent runners are serialised by a PostgreSQL advisory
   lock; migrations 0001–0006) and then boots uvicorn.
3. Vercel builds the SPA from the same commit.
4. **Rollback rule:** an image that includes a newer migration applies it at boot, and an older image cannot start against a newer revision;
   downgrades are destructive. Fix forward rather than using the dashboard "Rollback".
5. Take a database backup before any migration. **No production backup has been taken by the project**; a backup/restore procedure is **planned / deferred** and is not part of this release. Supabase native backups are UNVERIFIED.

## Production verification (manual)
1. `GET /health` returns `200` with `{"status":"healthy",…}`.
2. Open `/docs` and confirm the operation list loads.
3. Register/login in the SPA; confirm a protected call returns `200`, and the same call without a token returns `401`.
4. Run the checklist in [`SMOKE_CHECKLIST.md`](SMOKE_CHECKLIST.md) (eight workflows). The live smoke checklist has **not** been recorded as run.
5. Check the Render log for a clean Alembic run and a started server. The Sentry line `Sentry disabled (no SENTRY_DSN)` is the expected current value (verified locally with no DSN).

## Free-tier behaviour
- Render's free web service **sleeps after ~15 minutes idle**; the first request after sleep is slow while the container restarts. Cold-start duration was not measured.
- Limited memory and a single worker: long PDF/lecture jobs share the process with web requests; jobs are not resumable if the process restarts.
- Supabase, Qdrant Cloud and Upstash free-plan quotas are subject to each provider's current terms (not verified here).
- Password-reset email stays unavailable until SMTP is configured (`503`), and `REQUIRE_EMAIL_VERIFICATION` must remain `false` without SMTP.
