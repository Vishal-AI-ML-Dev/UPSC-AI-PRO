# API overview

The backend is a FastAPI application. This page is a map, not a copy of the OpenAPI schema; the generated, always-current
reference is the interactive documentation. Verified against `app.openapi()` and the middleware code on 2026-11-05.

| Item | Value |
|---|---|
| API base path | `/api/v1` |
| Production backend (owner-provided) | `https://upsc-ai-pro-puih.onrender.com` |
| Interactive API docs (Swagger UI) | `https://upsc-ai-pro-puih.onrender.com/docs` (ReDoc at `/redoc`, schema at `/openapi.json`) |
| Health | `GET https://upsc-ai-pro-puih.onrender.com/health` |
| Local | `http://127.0.0.1:8000` (`uv run uvicorn src.api.main:app --reload`) |

The production URLs were supplied by the repository owner and are not stored in the repository; their liveness was **not
probed** while writing this documentation (UNVERIFIED here). Note that `render.yaml` names the service `upsc-ai-pro-api`;
Render appends its own suffix to the public hostname, so the two names differing is expected rather than an error.

## Counts
**54** REST operations under `/api/v1` (56 including `/` and `/health`), across 16 route groups. `routes/mentor.py` exists
but is **not registered** and is not part of the API.

## Authentication
- `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/verify-email`, `/auth/resend-verification`,
  `/auth/forgot-password`, `/auth/reset-password`, and `GET /auth/me`. These are the only public routers.
- Login returns a short-lived **access token** (JWT, 30 minutes) and an opaque **refresh token** (30 days). Send the access
  token as `Authorization: Bearer <token>`.
- **Every other router** is registered in `src/api/main.py` under `_protected_routers` with `Depends(get_current_user)`.
  A missing, invalid or superseded token returns `401`; a failed session lookup fails closed (`503`), never open.
- Admin dashboards (`/cost/overview`, `/monitoring/overview`, `/experiments/overview`) additionally require an admin
  (`require_admin`, allow-listed by `ADMIN_EMAILS`); non-admins get `403`.
- Details: [SECURITY.md](SECURITY.md).

## Endpoint groups
| Group | Prefix | Operations (summary) |
|---|---|---|
| Auth | `/auth` | register, login, refresh, logout, me, email verification, password reset |
| AI Mentor | `/agent` | `POST /agent/chat/stream` |
| History | `/history` | list conversations, list messages, save message |
| NCERT | `/ncert` | classes, subjects, chapters, `study`, `chat` |
| PYQ | `/pyq` | generate, parse, topics, bank upload/generate/status/clear |
| Evaluator | `/evaluator` | `evaluate/sync`, `mains/sync`, `model-answer/sync` |
| Current Affairs | `/current-affairs` | daily, editorial, monthly, topics, dates, months |
| Planner | `/planner` | generate (stream and sync), plan get/put/delete, plan progress |
| Lecture | `/lecture` | process, process-text, chat |
| PDF chat | `/upload` | process, chat |
| Jobs | `/jobs` | `GET /jobs/{job_id}` (owner-scoped status/result) |
| Progress | `/progress` | `GET /progress/overview` (dashboard analytics) |
| Feedback | `/feedback` | `POST /feedback/submit` |
| Admin | `/cost`, `/monitoring`, `/experiments` | `*/access`, `*/overview` |

Per-workflow behaviour, request fields and output types: [WORKFLOWS.md](WORKFLOWS.md).

## Response conventions
- **Streaming endpoints** (`/agent/chat/stream`, `/ncert/chat`, `/pyq/generate`, `/pyq/bank/generate`,
  `/current-affairs/*` generators, `/planner/generate`, `/lecture/chat`, `/upload/chat`) return `text/plain` produced through
  `guard_stream`, which re-checks the session during the stream. Failures after the stream starts appear as a fixed notice in
  the text, not as an HTTP status.
- **JSON endpoints** use Pydantic models from `src/schemas.py`.
- **Background jobs:** `POST /upload/process`, `/lecture/process`, `/lecture/process-text`, `/pyq/bank/upload` return a
  `job_id`; poll `GET /jobs/{job_id}` until `done` or `error`. A job belongs to the user who created it.

## Errors
- Errors use `{"detail": "..."}`. Messages are fixed and user-facing; provider errors, stack traces, paths and SQL are not
  returned (unhandled exceptions return `500 {"detail": "Internal server error. Please try again."}`).
- `401` auth failure · `403` forbidden/unverified · `404` not found *or not yours* · `413` upload or body too large ·
  `422` validation (field limits) · `429` rate limited · `503` dependency unavailable (for example email delivery, auth DB).
- Validation limits are declared with Pydantic `Field(max_length=...)`; for example chat `question` ≤ 4,000 characters,
  lecture transcript ≤ 200,000, pasted PYQ text ≤ 50,000. A general 1 MiB request-body limit applies to non-upload bodies.

## Request IDs
Every response carries `X-Request-ID`. A safe inbound `X-Request-ID` is honoured; otherwise one is generated. It is attached
to every log line for that request and is the value to quote when reporting a problem.

## Rate limits
- Global per-client-IP limit: **100 requests / 60 s** (`RATE_LIMIT_REQUESTS`, `RATE_LIMIT_PERIOD`), exempting `/`, `/health`,
  `/docs`, `/redoc`, `/openapi.json`.
- Stricter auth limit: **10 requests / 300 s** on `login`, `register`, `forgot-password`, `reset-password`, `verify-email`,
  `resend-verification` (`AUTH_RATE_LIMIT_*`). `/auth/refresh` is intentionally under the global limit only.
- Exceeding a limit returns `429` with a `Retry-After` header. Counters are in-process unless Upstash Redis is configured, in
  which case they are shared across instances (production configuration UNVERIFIED).
- A 90-second hard timeout applies to each request.

## Examples
Verified request shape (no secrets; a real token is required for protected routes):

```bash
curl -s https://upsc-ai-pro-puih.onrender.com/health
# {"status":"healthy","version":"...","app":"..."}   (shape from src/api/main.py)

curl -N -X POST "$API/api/v1/agent/chat/stream" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"Explain the basic structure doctrine","thread_id":"demo"}'
```
Response bodies from live models are not reproduced here because none were captured from production.
