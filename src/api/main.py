"""FastAPI Main Application"""

import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.deps import get_current_user
from src.api.http_metrics_mw import HttpMetricsMiddleware
from src.api.rate_limit import RateLimitMiddleware
from src.api.request_id import RequestIdMiddleware

# Core routers are always present.
from src.api.routes import (
    auth,
    chat,
    current_affairs,
    evaluator,
    feedback,
    history,
    lecture,
    ncert,
    planner,
    pyq,
    upload,
)
from src.api.security_headers import (
    SecurityHeadersMiddleware,
    TimeoutMiddleware,
)
from src.api.upload_limit import MaxUploadSizeMiddleware
from src.core import observability
from src.core.config import settings
from src.core.db import init_db
from src.core.email_utils import check_email_config, smtp_configured
from src.core.error_monitoring import init_sentry, sentry_enabled
from src.core.job_handlers import register_all
from src.core.logging_config import setup_logging
from src.core.token_cleanup import purge_expired_tokens
from src.core.vector_store import ensure_vector_storage
from src.graph.app_graph import build_app
from src.graph.memory import close_memory

# -------------------------------------------------------------------
# LOGGING
# -------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)


def _load_optional_routers(names):
    """Import optional route modules by name, skipping any that are absent.

    Keeps the app booting on older checkouts that do not yet ship the admin
    dashboard / background-job routes (cost, experiments, monitoring, etc.).
    """
    modules = []
    for name in names:
        try:
            modules.append(importlib.import_module(f"src.api.routes.{name}"))
        except Exception:
            logger.warning("Optional route module '%s' not available; skipping", name)
    return modules


# Error monitoring (Sentry). No-op when SENTRY_DSN is unset; fail-open on error.
init_sentry()


# -------------------------------------------------------------------
# LIFESPAN
# -------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info(f"\U0001f680 Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"\U0001f4cd API Prefix: {settings.api_prefix}")
    logger.info(f"\U0001f527 Debug Mode: {settings.debug}")
    logger.info(f"\U0001f4ca Langfuse: {observability.langfuse_enabled()}")
    logger.info(f"\U0001f6a8 Sentry: {sentry_enabled()}")
    # Production + required verification + no SMTP => refuse to start.
    check_email_config()
    init_db()
    # Best-effort purge of dead auth-token rows on boot. The import is now hard
    # (a core feature); only the DB call is guarded so a transient DB hiccup
    # cannot block boot. A cron / scripts/cron_cleanup.py can also run this.
    try:
        purge_expired_tokens()
    except Exception:
        logger.warning("Startup token cleanup skipped", exc_info=True)
    # Register background job handlers so the web process and any worker can
    # execute enqueued jobs. Import + registration are required (no fail-open).
    register_all()
    ensure_vector_storage()
    # Build the LangGraph supervisor once (checkpointer + long-term store wired)
    # and reuse it across all requests.
    app.state.agent_graph = build_app()
    logger.info("\U0001f916 Agent graph ready (supervisor + memory wired)")
    if settings.require_email_verification and not smtp_configured():
        logger.warning(
            "REQUIRE_EMAIL_VERIFICATION is on but SMTP is not configured -> "
            "email verification auto-disabled (NON-PRODUCTION ONLY; production "
            "refuses to start). Configure SMTP_* to test the verification flow."
        )
    yield
    logger.info("\U0001f44b Shutting down...")
    observability.flush()  # push any pending Langfuse traces
    close_memory()  # release LangGraph DB pools cleanly


# -------------------------------------------------------------------
# APP
# -------------------------------------------------------------------
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-Powered UPSC Preparation System",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# -------------------------------------------------------------------
# CORS
# -------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let the allowed frontends read the 401 session reason
    # (error_description="session_superseded" etc.); origins are unchanged.
    expose_headers=["WWW-Authenticate"],
)

# -------------------------------------------------------------------
# SECURITY HEADERS + REQUEST TIMEOUT (added by deployment hardening)
# -------------------------------------------------------------------
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TimeoutMiddleware, timeout_seconds=90)

# -------------------------------------------------------------------
# RATE LIMITING (per IP: rate_limit_requests / rate_limit_period)
# -------------------------------------------------------------------
app.add_middleware(RateLimitMiddleware)

# -------------------------------------------------------------------
# MAX UPLOAD SIZE (reject files larger than max_upload_mb)
# -------------------------------------------------------------------
app.add_middleware(MaxUploadSizeMiddleware)

# -------------------------------------------------------------------
# HTTP METRICS (roadmap #18) - admin-only monitoring dashboard
# -------------------------------------------------------------------
# Records latency / status / endpoint per request.
app.add_middleware(HttpMetricsMiddleware)

# -------------------------------------------------------------------
# REQUEST ID / TRACING
# -------------------------------------------------------------------
# Added last so it is the OUTERMOST middleware: every inner log line and the
# response carry a correlation ID (inbound X-Request-ID honored, else generated).
app.add_middleware(RequestIdMiddleware)


# -------------------------------------------------------------------
# GLOBAL EXCEPTION HANDLER
# -------------------------------------------------------------------
# Catch-all for *unhandled* exceptions so the API always returns a clean JSON
# envelope and never leaks a stack trace to the client. FastAPI's own handlers
# for HTTPException (401/403/404/...) and request validation (422) still run
# as normal; this only fires for unexpected 500-class errors, which are logged
# server-side with the request method + path for debugging.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again."},
    )


# -------------------------------------------------------------------
# PUBLIC ROUTES (no auth)
# -------------------------------------------------------------------
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "app": settings.app_name,
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": f"Welcome to {settings.app_name}",
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


# Auth router - OPEN (login yahin hota hai, protect nahi kar sakte)
app.include_router(auth.router, prefix=settings.api_prefix)

# -------------------------------------------------------------------
# PROTECTED ROUTES (JWT required)
# -------------------------------------------------------------------
_protected_routers = [
    planner,
    ncert,
    lecture,
    current_affairs,
    upload,
    pyq,
    evaluator,
    history,
    feedback,
    chat,
]

# Optional routers (admin dashboards + background jobs). Silently skipped if the
# corresponding module is not present in this checkout, so the app always boots.
_protected_routers += _load_optional_routers(
    ["progress", "cost", "jobs", "experiments", "monitoring"]
)

for module in _protected_routers:
    app.include_router(
        module.router,
        prefix=settings.api_prefix,
        dependencies=[Depends(get_current_user)],
    )
