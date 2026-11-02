# Backend rules (`src/`)

## Structure
- Keep the root `src/` layout (ADR-001). New code goes into the existing layer:
  - HTTP: `src/api/routes/<feature>.py` — thin: validate input, check auth/ownership, call a function, shape the response.
  - Feature logic: `src/agents/<feature>/graph.py` (+ `prompts.py`).
  - LangGraph orchestration: `src/graph/`.
  - Shared services: `src/core/`.
  - API contracts: `src/schemas.py`. ORM tables: `src/core/models.py`.
- Register every new router in `src/api/main.py` under `_protected_routers` (JWT-protected by default).
  Public routes are an explicit, spec-approved exception.

## Reuse before writing
| Need | Use |
|---|---|
| Chat model | `get_llm()`, `get_fast_llm()`, `get_llm_for_tier()` — `src/core/llm.py` (fallback + timeout guard built in) |
| Model text from a response | `message_text()` — `src/core/llm.py` |
| Structured output with fallbacks | the `_structured_llm(schema)` pattern in `src/graph/supervisor.py` / `rag_graph.py` |
| Vector store | `load_vector_store`, `upsert_documents`, `create_vector_store`, `similarity_search_with_sources`, `make_persist_key` — `src/core/vector_store.py` |
| Untrusted text into a prompt | `harden_untrusted()`, `format_chat_history()` — `src/core/prompt_safety.py` |
| Web search | `search_web()` — `src/core/web_search.py` |
| Citations / trust notes | `src/core/grounding.py` |
| Current user / admin | `get_current_user` (`src/api/deps.py`), `require_admin` (`src/api/admin_access.py`) |
| Background work | `enqueue()` / `get_job()` — `src/core/job_queue.py` |
| Settings | `from src.core.config import settings` — add new settings there with a safe default |

## Request handling
- Blocking work (sync SQLAlchemy, pypdf, LLM `.invoke`, SMTP, file I/O) must run in a plain `def` handler
  (FastAPI threadpool) or be offloaded — never directly inside `async def`.
- Streaming endpoints return `StreamingResponse(guard_stream(gen(), user), media_type="text/plain")`.
- Never put raw exception text in an HTTP response (`detail=str(e)` only for our own fixed user-facing
  `ValueError`/domain messages). Log with `describe_exception()` for provider errors.
- Validate input size with Pydantic `Field(max_length=...)` on every free-text field you add or touch.

## Data
- Every schema change ships an Alembic migration in `migrations/versions/` (ask before editing; never run it).
- Every user-owned row has a `user_id`, and every query filters by it (pattern: `src/core/history.py:get_conversation`).
- Do not add LangGraph/vector data paths that bypass ownership (see `security.md`).

## LLM & agents
- Prompts live in `src/agents/<feature>/prompts.py`. Untrusted content (uploads, web, transcripts, KB) is always fenced.
- Do not claim a graph is "multi-agent" in docs or code comments unless it actually is.
- Fail-open is fine for optional dependencies (cache, tracing); auth and ownership checks fail closed.

## Style
- Python 3.13, ruff line length 100, ruff rules E/F/W/I (`pyproject.toml`). The format hook runs `ruff format`
  on edited files only.
- Logging: `logger = logging.getLogger(__name__)`; no secrets, tokens, emails or user message text in logs.
