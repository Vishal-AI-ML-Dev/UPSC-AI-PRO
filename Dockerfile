# syntax=docker/dockerfile:1
# ============================================================================
# UPSC Agentic AI — Backend (FastAPI + LangGraph) production image
# Multi-stage, uv-based, non-root, slim. Targets src.api.main:app
# ============================================================================

# ---------- Stage 1: build deps with uv into a venv ----------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Build tools needed by some wheels (psycopg, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast, matches render.yaml/CI toolchain)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install locked deps first for better layer caching.
COPY pyproject.toml uv.lock ./
# Install the frozen dependency set into /opt/venv (uv's project env; VIRTUAL_ENV is ignored by uv sync).
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-install-project && \
    /opt/venv/bin/alembic --version && /opt/venv/bin/uvicorn --version

# ---------- Stage 2: slim runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    HOME=/home/user \
    XDG_CACHE_HOME=/home/user/.cache \
    HF_HOME=/home/user/.cache/huggingface \
    MPLCONFIGDIR=/home/user/.cache/matplotlib \
    FASTEMBED_CACHE_DIR=/home/user/.cache/fastembed \
    PORT=7860

# libpq for psycopg runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Bring the pre-built venv over.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Run as non-root. HF Spaces convention: uid 1000, writable /home/user.
RUN useradd -m -u 1000 user && \
    mkdir -p /home/user/.cache && \
    chown -R user:user /app /home/user
USER user

# HF Spaces routes to app_port (see README frontmatter). Keep in sync with PORT.
EXPOSE 7860

# Container-level health check hitting the FastAPI /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/health || exit 1

# Apply DB migrations (idempotent) then boot. Shell form so $PORT expands.
# Use 1 worker on 512MB free tiers; raise WEB_CONCURRENCY on bigger plans.
# Behind a reverse proxy, set FORWARDED_ALLOW_IPS to the proxy's address range
# (never "*") so the rate limiter sees real client IPs; uvicorn's default
# (127.0.0.1) ignores X-Forwarded-For from anywhere else.
CMD alembic upgrade head && \
    uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT} \
    --workers ${WEB_CONCURRENCY:-1} --timeout-keep-alive 30 --proxy-headers
