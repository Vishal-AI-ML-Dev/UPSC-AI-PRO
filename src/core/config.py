"""Core configuration - Pydantic Settings"""

import logging
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from src.core.secret_utils import resolve_jwt_secret

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "UPSC AI Pro"
    app_version: str = "2.0.0"
    debug: bool = False
    # Explicit deployment environment: "production" | "staging" | "development".
    # Empty => inferred from `debug`. This is the AUTHORITATIVE signal for
    # security-sensitive gates (e.g. JWT-secret strictness) so we never rely on
    # the debug flag alone to detect production. Set ENV=production in prod.
    env: str = ""

    # API
    api_prefix: str = "/api/v1"

    # LLM - Google Gemini
    google_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_fast_model: str = "gemini-2.5-flash-lite"
    llm_temperature: float = 0.3
    llm_max_retries: int = 1  # fail fast -> fallback provider (quota errors dont recover on retry)
    # Max output tokens for EACH Gemini chat-model call (main, fast and fallback
    # models). Must be 1..65536; invalid values stop startup. This bounds a
    # single generation only - retries, provider fallback and multiple agent
    # calls per request each add their own tokens, so it is NOT a total
    # per-request cost cap.
    llm_max_output_tokens: int = Field(default=4096, gt=0, le=65536)
    # Per-call Gemini network timeout in SECONDS (0 < value <= 600). Applies to
    # connecting and to EACH wait for data, incl. the wait for a streamed
    # response to start and every gap between streamed chunks. It is NOT a
    # whole-request deadline: a steadily streaming answer is not cut off, and a
    # request with several LLM calls can take several timeouts in total. A timed
    # out call is not retried and does not fall back to another model.
    llm_request_timeout_seconds: float = Field(default=45.0, gt=0, le=600)

    # LLM fallback provider - Groq (free tier; used when Gemini hits 429)
    # Free key: https://console.groq.com  |  empty key => Gemini-only (no change)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_fast_model: str = "llama-3.1-8b-instant"
    groq_whisper_model: str = "whisper-large-v3-turbo"  # Groq Speech-to-Text (audio -> text)
    enable_provider_fallback: bool = True
    # Extra free Groq models appended to BOTH fallback chains (CSV). Reuses
    # GROQ_API_KEY - deepens resilience at zero cost when Gemini + the primary
    # Groq models are rate-limited. Empty => no extra models.
    groq_fallback_models: str = "gemma2-9b-it"

    # OpenAI-compatible CLOUD LLM providers (NVIDIA NIM, OpenRouter, Cerebras,
    # Together, Fireworks, Mistral, DeepInfra, ...). They all speak the OpenAI
    # /v1 API, so ONE config points at any of them. Models listed (CSV) are
    # appended to BOTH LLM fallback chains. Needs: uv add langchain-openai.
    # e.g. NVIDIA: OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_models: str = ""

    # Embeddings
    # Provider: "gemini" (API), "local" (FastEmbed offline), or "ollama"
    # (local Ollama server, open-source models). Default = gemini.
    embedding_provider: str = "gemini"
    # Gemini embedding model. NOTE: gemini-embedding-001 is the one actually
    # served on the current free API (text-embedding-004 returned 404 there).
    embedding_model: str = "models/gemini-embedding-001"
    # Per-request Gemini EMBEDDING timeout in SECONDS (0 < value <= 600). Passed
    # to google-genai as HttpOptions.timeout (milliseconds) and applied by httpx
    # to connect/read/write. One attempt per request (no SDK retry). A batch of
    # up to 100 texts is one request.
    embedding_request_timeout_seconds: float = Field(default=45.0, gt=0, le=600)
    # Local FastEmbed model when embedding_provider="local" (needs: uv add fastembed).
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Optional dir holding a PRE-DOWNLOADED FastEmbed model, for networks that
    # block HuggingFace. Empty => normal HF download/cache.
    fastembed_cache_dir: str = ""
    # Ollama local server (open-source models via `ollama pull`, NOT HuggingFace).
    # Used when embedding_provider="ollama".
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"
    # Cloud embeddings via any OpenAI-compatible /v1 endpoint (e.g. NVIDIA
    # nv-embedqa). Reachable when HuggingFace is blocked. Used when
    # embedding_provider="openai_compat". Needs: uv add langchain-openai.
    openai_embed_base_url: str = ""
    openai_embed_api_key: str = ""
    openai_embed_model: str = ""

    # Vector Store (Chroma = local dev fallback; Qdrant = production/cloud)
    chroma_persist_dir: str = "chroma_db"
    chunk_size: int = 1000
    chunk_overlap: int = 150

    # Qdrant managed vector DB. Empty qdrant_url => automatically falls back to
    # local Chroma (so local dev keeps working without Qdrant).
    #   Cloud free cluster:
    #     QDRANT_URL=https://<id>.<region>.aws.cloud.qdrant.io:6333
    #     QDRANT_API_KEY=<your-api-key>
    #   Self-hosted later (e.g. Oracle VM): QDRANT_URL=http://localhost:6333
    #     (api key optional for a private self-hosted instance)
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    # Network timeout (seconds) for Qdrant client operations.
    qdrant_timeout: int = 30
    # Embedded local Qdrant (in-process, no server, no network): a directory
    # path, e.g. QDRANT_PATH=D:/data/qdrant. Set QDRANT_URL OR QDRANT_PATH, never
    # both. SINGLE-PROCESS ONLY: the folder is locked by the first process that
    # opens it, so a second app worker, an RQ worker or a script using the same
    # path fails while the app runs. Meant for local development / one-process
    # previews, not for multi-worker deployments.
    qdrant_path: str = ""

    # Output size Gemini embeddings are pinned to (Matryoshka truncation).
    # Also the vector size used to create/validate Qdrant collections, so no
    # probe embedding call is needed. 0 = no pinning (size then has to be probed).
    gemini_embedding_dim: int = Field(default=768, ge=0, le=3072)

    # NCERT content library (folder of class/subject/chapter PDFs).
    # Empty/missing => NCERT browse shows no content (app still runs, no crash).
    # In prod point to a persistent path, e.g. NCERT_DATA_DIR=/var/data/ncert
    ncert_data_dir: str = "data/ncert"

    # Uploads
    max_upload_mb: int = 50  # is se badi file -> 413 (Too Large). Override via MAX_UPLOAD_MB env.

    # Database (multi-user + history). Postgres in prod, SQLite local fallback.
    # e.g. DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
    database_url: str = ""

    # Live web search (Tavily AND the keyless DuckDuckGo fallback) for the
    # Mentor web_search tool / RAG web fallback, planner exam-date lookup and
    # monthly current-affairs ingest. false => neither provider is contacted.
    web_search_enabled: bool = True

    # Tavily Search
    tavily_api_key: str = ""

    # Rate Limiting
    rate_limit_requests: int = 100
    rate_limit_period: int = 60  # seconds
    # Stricter limit for sensitive auth endpoints (login/register/refresh/reset)
    # to blunt credential-stuffing & user-enumeration. Keyed per-IP, own window.
    auth_rate_limit_requests: int = 10
    auth_rate_limit_period: int = 300  # 5 minutes
    # Rate-limit backend: distributed (Upstash Redis) when creds present + this
    # is on, else in-process per-worker. Redis is required to be correct across >1 worker.
    rate_limit_redis_enabled: bool = True
    # Circuit breaker for the shared Upstash REST client (cache + rate limiter):
    # after this many consecutive failures, fail fast for reset_seconds so a
    # Redis outage never adds an HTTP timeout to the hot path.
    circuit_breaker_fail_max: int = 5
    circuit_breaker_reset_seconds: float = 30.0
    redis_rest_timeout_seconds: float = 2.0

    # CORS - sirf apne asli frontend origins (NOT "*" with credentials).
    # .env mein override kar sakte ho, e.g.:
    #   CORS_ORIGINS=["http://localhost:8501","https://myapp.com"]
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:8501",  # Streamlit default
        "http://127.0.0.1:8501",
        "http://localhost:3000",  # React/Next dev (agar use karo)
        "http://127.0.0.1:3000",
        "http://localhost:5173",  # Vite dev server (React frontend)
        "http://127.0.0.1:5173",
        "http://localhost:8080",
    ]

    # Observability - Langfuse (empty keys => tracing OFF, app unchanged)
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Logging - DEBUG/INFO/WARNING/ERROR; empty => auto from debug flag
    log_level: str = ""
    # Log output format: "json" | "text"; empty => json in prod, text in debug
    log_format: str = ""

    # Auth / JWT
    jwt_secret: str = ""  # .env se aayega
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30  # 30 min (rotating refresh token keeps sessions alive)
    # Refresh token validity (minutes) - default 30 days. Access tokens can
    # stay short-lived because a long-lived, ROTATING & REVOCABLE refresh
    # token (see /auth/refresh + /auth/logout) keeps sessions alive. Lower
    # access_token_expire_minutes to ~30 once the frontend wires up refresh.
    refresh_token_expire_minutes: int = 43200  # 30 days
    # Running streams re-check their session at most this often (seconds), so a
    # superseded/ended session stops receiving output within about this delay.
    stream_session_check_seconds: float = 5.0

    # Email (SMTP) - password reset links.
    # Empty SMTP creds => reset link is logged to the server console instead of emailed (dev).
    # Gmail: smtp_host=smtp.gmail.com, smtp_port=587, smtp_user=<you@gmail.com>,
    #   smtp_password=<16-char Gmail App Password>, smtp_from=<you@gmail.com>
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # blank => uses smtp_user
    smtp_use_tls: bool = True
    # Frontend base URL used to build the reset link (no trailing slash)
    frontend_url: str = "http://localhost:8501"
    # Password reset token validity (minutes)
    reset_token_expire_minutes: int = 30
    # Email verification token validity (minutes) - default 24 hours
    verification_token_expire_minutes: int = 1440
    # Require email verification before login (strict). Set REQUIRE_EMAIL_VERIFICATION=false in .env to disable.
    require_email_verification: bool = True

    # Response cache (Upstash Redis, REST) - skip re-running the agent graph for
    # a repeated identical question -> lower latency + LLM cost.
    # Empty creds OR response_cache_enabled=false => cache is a no-op (app unchanged).
    #   Free DB: https://console.upstash.com -> Redis -> REST API (URL + TOKEN)
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = 86400  # 24h
    response_cache_scope: str = "thread"  # thread | user ("global" is treated as user)
    # Semantic (embedding-similarity) cache: paraphrased questions reuse an
    # existing answer. Opt-in; exact-match behaviour is unchanged when off.
    response_cache_semantic: bool = True
    response_cache_semantic_threshold: float = 0.92  # cosine >= this => hit
    response_cache_semantic_max_index: int = 200  # per-scope index cap

    # Admin allowlist (JSON list of emails, same convention as CORS_ORIGINS).
    # These users - and only these - see the cost dashboard tab and can call
    # /cost/*. Empty => no admins (cost surfaces stay locked).
    #   e.g. ADMIN_EMAILS=["you@example.com"]
    admin_emails: Annotated[list[str], NoDecode] = []

    # Cost dashboard pricing - ₹ per 1,000 tokens (input/output) per tier.
    # Blended by the observed LITE-vs-STRONG mix. Override via env when prices
    # change; figures are estimates for a spend gauge, not billing.
    price_lite_input_inr: float = 0.006
    price_lite_output_inr: float = 0.024
    price_strong_input_inr: float = 0.025
    price_strong_output_inr: float = 0.100

    # Background job queue (#10). Heavy work (PDF/lecture processing, notes,
    # vector indexing) runs OFF the request path. The default 'thread' backend
    # needs NO extra infra and works on the free tier at Rs.0. Set REDIS_URL (a
    # TCP rediss:// url) + run a worker (src/worker.py) to switch to 'rq'.
    jobs_backend: str = "auto"  # auto | thread | rq | inline
    redis_url: str = ""  # TCP Redis url for rq (rediss://...)
    rq_queue_name: str = "upsc-jobs"
    job_max_retries: int = 1  # bounded retries on transient failure
    job_timeout_seconds: int = 900  # per-job hard cap (rq worker)

    # --- A/B prompt experiments (#12) ---
    experiments_enabled: bool = True  # master switch; control arm keeps todays prompt
    experiments_config: str = ""  # optional JSON overriding the built-in experiments

    # Reflection / self-critique (#7). After generation, a critic scores the
    # answer and (when weak) a bounded revise pass rewrites it. Fail-open: any
    # critic error keeps the original answer. reflection_enabled=false restores
    # the previous single-pass behaviour.
    reflection_enabled: bool = False
    reflection_min_score: int = 7  # 1-10; below this triggers a revise
    reflection_max_revisions: int = 1  # bounded loop (cost + latency guard)

    # Plan-and-execute (#7). For genuinely complex, multi-part questions the
    # mentor path can decompose -> execute each sub-step -> synthesize. OFF by
    # default because it issues several extra LLM calls (latency + free-tier
    # quota). Enable with PLAN_EXECUTE_ENABLED=true.
    plan_execute_enabled: bool = False
    plan_execute_min_words: int = 30  # complexity gate (aligns w/ model_router)
    plan_execute_max_steps: int = 5  # cap sub-steps (cost guard)
    # #5 Parallel execution. Independent sub-steps run concurrently (bounded)
    # instead of one-by-one. FAIL-OPEN -> sequential on any error. OFF by
    # default so behaviour is unchanged until opted in.
    plan_execute_parallel: bool = False
    plan_execute_max_concurrency: int = 3  # cap simultaneous sub-step LLM calls

    # ------------------------------------------------------------------ #
    # Advanced retrieval upgrades (RAG audit #1-#3). All OFF by default and
    # FAIL-OPEN, so the current RRF hybrid behaviour is unchanged until each
    # flag is switched on. No hard dependency is added; optional libs are
    # imported lazily with a clear message when a provider is enabled.
    # ------------------------------------------------------------------ #
    # #1 Cross-encoder reranking. After RRF fusion, a cross-encoder re-scores
    # (query, chunk) pairs for precise top-k ordering.
    #   provider=local  -> sentence-transformers CrossEncoder (uv add sentence-transformers)
    #   provider=cohere -> Cohere Rerank API (uv add cohere + COHERE_API_KEY)
    rerank_enabled: bool = False
    rerank_provider: str = "local"  # local | cohere
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = 5  # keep top-N after reranking
    cohere_api_key: str = ""
    cohere_rerank_model: str = "rerank-english-v3.0"

    # #2 Multi-query retrieval (RAG-Fusion). An LLM paraphrases the query into
    # N variants; each is retrieved and the candidate pools are merged before
    # RRF. Deepens recall for short/abbreviated queries (FR, DPSP, CAG...).
    multi_query_enabled: bool = False
    multi_query_count: int = 3  # total queries incl. original

    # #3 HyDE (Hypothetical Document Embeddings). An LLM drafts a hypothetical
    # answer that is embedded for the dense arm (answer-style text matches
    # documents better). One extra LLM call per query -> keep OFF on free tier.
    hyde_enabled: bool = False

    # #4 Human-in-the-loop. Planner/Evaluator can pause (LangGraph interrupt)
    # to ask for missing params, then resume. Needs a checkpointer + a
    # resume-capable client, so it is OFF by default.
    hitl_enabled: bool = False

    # PYQ second-pass fact-check. A verifier LLM re-checks generated MCQs
    # before display. OFF by default: it doubles LLM calls per generation
    # (free-tier token guard). Enable with PYQ_VERIFY_ENABLED=true.
    pyq_verify_enabled: bool = False

    # Error monitoring - Sentry (empty SENTRY_DSN => disabled, app unchanged).
    # Free Developer plan: 5K errors/month. FastAPI + Starlette integrations
    # auto-enable. Fail-open: any init error never blocks app boot.
    #   Free project + DSN: https://sentry.io  (create a Python -> FastAPI project)
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.0  # perf tracing off by default (cost)
    sentry_profiles_sample_rate: float = 0.0

    @field_validator("cors_origins", "admin_emails", mode="before")
    @classmethod
    def _parse_list_env(cls, v):
        """Accept a JSON array OR a plain comma-separated string from env.

        Hosting dashboards make strict JSON easy to get wrong, so both
        CORS_ORIGINS=["https://a.com","https://b.com"] and
        CORS_ORIGINS=https://a.com,https://b.com are accepted. Quotes and
        stray whitespace are stripped defensively.
        """
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                import json

                try:
                    return json.loads(s)
                except Exception:
                    s = s.strip("[]")
            return [
                item.strip().strip('"').strip("'")
                for item in s.split(",")
                if item.strip().strip('"').strip("'")
            ]
        return v

    @property
    def is_production(self) -> bool:
        """True in production. Prefers explicit ENV; falls back to the debug flag.

        Relying on ``debug`` alone is fragile (staging can accidentally run with
        debug=False); an explicit ENV=production is the authoritative signal for
        security-sensitive gates such as JWT-secret strictness.
        """
        env = (self.env or "").strip().lower()
        if env:
            return env in ("production", "prod", "live")
        return not self.debug

    @model_validator(mode="after")
    def _normalize_env_values(self):
        """Clean secrets/URLs pasted into hosting dashboards.

        Hosting UIs (Render etc.) often capture surrounding quotes or stray
        whitespace. Strip them so downstream clients (SQLAlchemy, psycopg,
        Qdrant) receive valid values. Also upgrade the legacy ``postgres://``
        scheme that SQLAlchemy 2.0 no longer accepts.
        """

        def _clean(value):
            if isinstance(value, str):
                return value.strip().strip('"').strip("'").strip()
            return value

        for field_name in (
            "database_url",
            "qdrant_url",
            "qdrant_path",
            "qdrant_api_key",
            "google_api_key",
            "groq_api_key",
            "tavily_api_key",
            "jwt_secret",
            "langfuse_public_key",
            "langfuse_secret_key",
            "langfuse_host",
            "embedding_model",
            "embedding_provider",
            "local_embedding_model",
            "groq_fallback_models",
            "fastembed_cache_dir",
            "ollama_base_url",
            "ollama_embedding_model",
            "openai_compat_base_url",
            "openai_compat_api_key",
            "openai_compat_models",
            "openai_embed_base_url",
            "openai_embed_api_key",
            "openai_embed_model",
            "upstash_redis_rest_url",
            "upstash_redis_rest_token",
            "redis_url",
            "rq_queue_name",
            "experiments_config",
            "sentry_dsn",
            "sentry_environment",
            "env",
            "rerank_provider",
            "rerank_model",
            "cohere_api_key",
            "cohere_rerank_model",
        ):
            setattr(self, field_name, _clean(getattr(self, field_name)))

        if self.database_url.startswith("postgres://"):
            self.database_url = "postgresql://" + self.database_url[len("postgres://") :]

        if self.qdrant_url and self.qdrant_path:
            raise ValueError(
                "Set either QDRANT_URL (remote Qdrant) or QDRANT_PATH (embedded local "
                "Qdrant), not both."
            )

        # SECURITY: never allow a wildcard origin together with credentials.
        # Browsers block "*" + credentials anyway, and it is a CSRF foot-gun, so
        # drop any wildcard defensively and keep only explicit origins.
        if "*" in self.cors_origins:
            self.cors_origins = [o for o in self.cors_origins if o != "*"]
            logger.warning(
                "CORS wildcard '*' is not allowed with credentials; dropped it. "
                "Set explicit CORS_ORIGINS for your frontend."
            )

        # Fail fast on a forgeable JWT secret in production; ephemeral in dev.
        # is_production prefers explicit ENV over the debug flag.
        self.jwt_secret = resolve_jwt_secret(self.jwt_secret, is_production=self.is_production)

        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
